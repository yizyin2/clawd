/* Clawd Windows：把当前工作区里窗口的位置通过 D-Bus 告诉 Clawd 桌宠。
 * Wayland 下普通程序看不到别的窗口在哪，只有 GNOME Shell 自己知道。
 * 只发 (id, x, y, 宽, 高)，从下往上排，不发标题和内容。
 *
 * 另外替 Clawd 管鼠标：Wayland 下 Xwayland 窗口自己设的点击区域会被每一帧的画面更新冲掉，
 * 整个窗口都挡鼠标。所以鼠标不在 Clawd 身上时，这里把它的窗口设成不接收鼠标，点击落到后面的窗口。 */

const { Clutter, Gio, GLib, Meta } = imports.gi;

const BUS_NAME = 'org.clawd.Windows';
const OBJECT_PATH = '/org/clawd/Windows';
const IFACE = `
<node>
  <interface name="org.clawd.Windows">
    <method name="GetWindows">
      <arg type="a(tiiii)" direction="out" name="windows"/>
    </method>
    <signal name="Changed">
      <arg type="a(tiiii)" name="windows"/>
    </signal>
    <method name="TrackPet">
      <arg type="t" direction="in" name="xid"/>
      <arg type="u" direction="in" name="pid"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
    </method>
  </interface>
</node>`;

const BUTTONS = Clutter.ModifierType.BUTTON1_MASK | Clutter.ModifierType.BUTTON2_MASK |
    Clutter.ModifierType.BUTTON3_MASK;

// 这些窗口有实体，Clawd 能站上去；菜单、提示框之类的不算
const SOLID = [
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.UTILITY,
];

// 窗口身上这些变化一发生就通知 Clawd
const WINDOW_SIGNALS = ['position-changed', 'size-changed', 'notify::minimized', 'workspace-changed'];

class ClawdWindows {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._last = '';
        this._later = 0;
        this._tracked = new Map();
        global.get_window_actors().forEach(actor => this._track(actor.meta_window));
        this._globalSignals = [
            [global.display, global.display.connect('window-created', (_display, w) => {
                this._track(w);
                this._schedule();
            })],
            [global.display, global.display.connect('restacked', () => this._schedule())],
            [global.workspace_manager,
                global.workspace_manager.connect('active-workspace-changed', () => this._schedule())],
        ];
        // 万一漏了哪种变化，每 0.25 秒兜底看一眼
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            this._poll();
            return GLib.SOURCE_CONTINUE;
        });
        this._pet = null;
        this._petTimer = 0;
        this._petActor = null;
        this._petSaved = [];
        this._petOn = true;
    }

    disable() {
        GLib.source_remove(this._timer);
        this._timer = 0;
        if (this._petTimer)
            GLib.source_remove(this._petTimer);
        this._petTimer = 0;
        this._releasePet();
        if (this._later)
            Meta.later_remove(this._later);
        this._later = 0;
        this._globalSignals.forEach(([obj, id]) => obj.disconnect(id));
        this._tracked.forEach((ids, w) => ids.forEach(id => w.disconnect(id)));
        this._tracked.clear();
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = null;
    }

    _track(w) {
        // Clawd 自己是 override-redirect 窗口，它动不用通知它自己
        if (this._tracked.has(w) || w.is_override_redirect())
            return;
        const ids = WINDOW_SIGNALS.map(signal => w.connect(signal, () => this._schedule()));
        ids.push(w.connect('unmanaged', () => {
            ids.forEach(id => w.disconnect(id));
            this._tracked.delete(w);
            this._schedule();
        }));
        this._tracked.set(w, ids);
    }

    // 拖窗口时一帧里可能动好几次：合并成一次，赶在这一帧画出来之前发给 Clawd，
    // 它就能和窗口在同一帧或下一帧挪到位
    _schedule() {
        if (this._later)
            return;
        this._later = Meta.later_add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._later = 0;
            this._poll();
            return GLib.SOURCE_REMOVE;
        });
    }

    GetWindows() {
        return this._windows();
    }

    _windows() {
        const workspace = global.workspace_manager.get_active_workspace();
        const windows = workspace.list_windows().filter(w =>
            !w.minimized &&
            w.showing_on_its_workspace() &&
            !w.is_override_redirect() &&
            SOLID.includes(w.get_window_type()) &&
            // 桌面图标那层也是个普通窗口，只是不进任务栏
            !(w.is_skip_taskbar() && w.get_window_type() === Meta.WindowType.NORMAL));
        return global.display.sort_windows_by_stacking(windows).map(w => {
            const r = w.get_frame_rect();
            return [w.get_id(), r.x, r.y, r.width, r.height];
        });
    }

    // Clawd 告诉我们它是哪个窗口，以及身体在窗口里的位置（只有这块能点）
    TrackPet(xid, pid, x, y, width, height) {
        this._pet = { desc: `0x${xid.toString(16)}`, pid, x, y, width, height, lost: 0 };
        if (!this._petTimer)
            this._petTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => this._updatePet());
    }

    _updatePet() {
        const pet = this._pet;
        const actor = global.get_window_actors().find(a => {
            const w = a.get_meta_window();
            return w && (w.get_description() === pet.desc || w.get_pid() === pet.pid && w.get_title() === 'Clawd');
        });
        if (!actor) {
            this._releasePet();
            // 窗口没了两秒还没回来（Clawd 退出了），就不看了；它再启动时会重新叫我们
            if (++pet.lost > 120) {
                this._petTimer = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        }
        pet.lost = 0;
        const r = actor.get_meta_window().get_frame_rect();
        const [px, py, mods] = global.get_pointer();
        const over = px >= r.x + pet.x && px < r.x + pet.x + pet.width &&
            py >= r.y + pet.y && py < r.y + pet.y + pet.height;
        // 在 Clawd 身上按下去开始拖的，拖出身体范围也不能放手
        const dragging = this._petOn && (mods & BUTTONS) !== 0;
        this._setPetInput(actor, over || dragging);
        return GLib.SOURCE_CONTINUE;
    }

    _setPetInput(actor, on) {
        if (actor !== this._petActor) {
            this._releasePet();
            this._petActor = actor;
            // 只动原本接收鼠标的那几层（实际上是窗口画面那一层），放手时原样还回去
            this._petSaved = [actor, ...actor.get_children()].filter(a => a.reactive);
            this._petOn = true;
        }
        if (on === this._petOn)
            return;
        this._petOn = on;
        this._petSaved.forEach(a => {
            a.reactive = on;
        });
        // 不等鼠标再动一下，马上重新判断鼠标下面是谁
        try {
            global.stage.repick_device(Clutter.get_default_backend().get_default_seat().get_pointer());
        } catch (e) {
            logError(e, 'clawd-windows: repick');
        }
    }

    _releasePet() {
        this._petSaved.forEach(a => {
            a.reactive = true;
        });
        this._petSaved = [];
        this._petActor = null;
        this._petOn = true;
    }

    _poll() {
        if (!this._dbus)
            return;
        const windows = this._windows();
        const key = JSON.stringify(windows);
        if (key === this._last)
            return;
        this._last = key;
        this._dbus.emit_signal('Changed', new GLib.Variant('(a(tiiii))', [windows]));
    }
}

function init() {
    return new ClawdWindows();
}
