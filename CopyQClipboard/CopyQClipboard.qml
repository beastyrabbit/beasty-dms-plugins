import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins
import qs.Modals.Common
PluginComponent {
    id: root

    layerNamespacePlugin: "copyq-clipboard"

    // --- Modal interface properties ---
    property string activeTab: "recents"
    property bool showKeyboardHints: false
    property int activeImageLoads: 0
    readonly property int maxConcurrentLoads: 3
    readonly property bool wtypeAvailable: SessionService.wtypeAvailable
    property int totalCount: 0
    property var clipboardEntries: []
    property var unpinnedEntries: []
    property var pinnedEntries: []
    property int pinnedCount: 0
    property int selectedIndex: 0
    property bool keyboardNavigationActive: false
    property string searchText: ""
    property var modalFocusScope: null

    // Internal data
    property var _internalEntries: []
    property var _internalPinnedEntries: []
    readonly property int _longTextThreshold: 200

    signal opened()

    // --- Bar pills ---
    horizontalBarPill: Component {
        DankIcon {
            name: "content_paste"
            size: root.iconSize
            color: Theme.surfaceText
        }
    }

    verticalBarPill: Component {
        DankIcon {
            name: "content_paste"
            size: root.iconSize
            color: Theme.surfaceText
        }
    }

    // Right-click opens CopyQ's own window
    pillRightClickAction: function() {
        Quickshell.execDetached(["copyq", "show"]);
    }

    // --- Popout ---
    popoutWidth: 550
    popoutHeight: 500

    popoutContent: Component {
        FocusScope {
            id: contentFocusScope

            property var closePopout: null
            property var parentPopout: null

            implicitWidth: root.popoutWidth
            implicitHeight: root.popoutHeight
            width: parent ? parent.width : root.popoutWidth
            height: root.popoutHeight
            focus: true

            LayoutMirroring.enabled: I18n.isRtl
            LayoutMirroring.childrenInherit: true

            Component.onCompleted: {
                root.modalFocusScope = contentFocusScope;
                root._onPopoutOpened();
            }

            onParentPopoutChanged: {
                if (parentPopout && parentPopout.shouldBeVisible) {
                    root._onPopoutOpened();
                }
            }

            Connections {
                target: parentPopout
                enabled: parentPopout !== null

                function onShouldBeVisibleChanged() {
                    if (parentPopout.shouldBeVisible) {
                        root._onPopoutOpened();
                        Qt.callLater(() => {
                            contentFocusScope.forceActiveFocus();
                            if (clipboardContentItem.searchField) {
                                clipboardContentItem.searchField.text = "";
                                clipboardContentItem.searchField.forceActiveFocus();
                            }
                        });
                    } else {
                        root._onPopoutClosed();
                    }
                }
            }

            Keys.onPressed: function(event) {
                keyboardController.handleKey(event);
            }

            ClipboardContent {
                id: clipboardContentItem
                modal: root
                clearConfirmDialog: confirmModal
            }

            ClipboardKeyboardController {
                id: keyboardController
                modal: root
            }
        }
    }

    // --- Confirm dialog ---
    ConfirmModal {
        id: confirmModal
        confirmButtonText: I18n.tr("Clear All")
        confirmButtonColor: Theme.primary
    }

    // --- Polling timer ---
    Timer {
        id: pollTimer
        interval: 3000
        repeat: true
        running: false
        onTriggered: root.refresh()
    }

    // --- CopyQ Backend: Fetch processes ---

    Process {
        id: fetchMainProcess
        command: ["copyq", "eval", "var r=[];var n=Math.min(count(),200);for(var i=0;i<n;i++){var t=str(read(i));var mimes=str(read('?',i));var img=mimes.indexOf('image/')>=0;r.push({id:i,row:i,preview:t.substring(0,100),size:t.length,isImage:img,pinned:false,hash:''});}print(JSON.stringify(r));"]
        running: false
        property string _buffer: ""

        stdout: SplitParser {
            onRead: data => { fetchMainProcess._buffer += data; }
        }

        onExited: (exitCode, exitStatus) => {
            if (exitCode === 0 && fetchMainProcess._buffer.trim()) {
                try {
                    root._internalEntries = JSON.parse(fetchMainProcess._buffer.trim());
                } catch(e) {
                    console.warn("CopyQClipboard: Failed to parse main entries:", e);
                    root._internalEntries = [];
                }
            }
            fetchMainProcess._buffer = "";
            fetchPinnedProcess._buffer = "";
            fetchPinnedProcess.running = true;
        }
    }

    Process {
        id: fetchPinnedProcess
        command: ["copyq", "eval", "tab('Saved');var r=[];var n=count();for(var i=0;i<n;i++){var t=str(read(i));r.push({id:1000+i,row:i,preview:t.substring(0,100),size:t.length,isImage:false,pinned:true,hash:''});}print(JSON.stringify(r));"]
        running: false
        property string _buffer: ""

        stdout: SplitParser {
            onRead: data => { fetchPinnedProcess._buffer += data; }
        }

        onExited: (exitCode, exitStatus) => {
            if (exitCode === 0 && fetchPinnedProcess._buffer.trim()) {
                try {
                    root._internalPinnedEntries = JSON.parse(fetchPinnedProcess._buffer.trim());
                } catch(e) {
                    console.warn("CopyQClipboard: Failed to parse pinned entries:", e);
                    root._internalPinnedEntries = [];
                }
            } else {
                root._internalPinnedEntries = [];
            }
            fetchPinnedProcess._buffer = "";
            root._mergeAndUpdate();
        }
    }

    // --- CopyQ Backend: Action process ---

    Process {
        id: actionProcess
        command: []
        running: false
        property var _callback: null

        onExited: (exitCode, exitStatus) => {
            if (actionProcess._callback) {
                actionProcess._callback(exitCode);
                actionProcess._callback = null;
            }
        }
    }

    // --- Paste support ---

    Process {
        id: wtypeProcess
        command: ["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"]
        running: false
    }

    Timer {
        id: pasteTimer
        interval: 200
        repeat: false
        onTriggered: wtypeProcess.running = true
    }

    // --- Modal interface methods ---

    function refresh() {
        if (fetchMainProcess.running || fetchPinnedProcess.running) return;
        fetchMainProcess._buffer = "";
        fetchPinnedProcess._buffer = "";
        fetchMainProcess.running = true;
    }

    function _mergeAndUpdate() {
        const all = root._internalEntries.concat(root._internalPinnedEntries);
        pinnedEntries = root._internalPinnedEntries;
        pinnedCount = pinnedEntries.length;
        _updateFilteredModel(all);
    }

    function updateFilteredModel() {
        const all = root._internalEntries.concat(root._internalPinnedEntries);
        _updateFilteredModel(all);
    }

    function _updateFilteredModel(allEntries) {
        const query = searchText.trim();
        let filtered;

        if (query.length === 0) {
            filtered = allEntries;
        } else {
            const lowerQuery = query.toLowerCase();
            filtered = allEntries.filter(entry => entry.preview.toLowerCase().includes(lowerQuery));
        }

        filtered.sort((a, b) => {
            if (a.pinned !== b.pinned) return b.pinned ? 1 : -1;
            return a.row - b.row;
        });

        clipboardEntries = filtered;
        unpinnedEntries = filtered.filter(e => !e.pinned);
        totalCount = unpinnedEntries.length;

        if (unpinnedEntries.length === 0) {
            keyboardNavigationActive = false;
            selectedIndex = 0;
            return;
        }
        if (selectedIndex >= unpinnedEntries.length) {
            selectedIndex = unpinnedEntries.length - 1;
        }
    }

    function _onPopoutOpened() {
        refresh();
        activeImageLoads = 0;
        _reset();
        opened();
        pollTimer.start();
    }

    function _onPopoutClosed() {
        pollTimer.stop();
        activeImageLoads = 0;
        _reset();
    }

    function show() {
        refresh();
        activeImageLoads = 0;
        _reset();
    }

    function hide() {
        closePopout();
        _onPopoutClosed();
    }

    function _reset() {
        searchText = "";
        selectedIndex = 0;
        keyboardNavigationActive = false;
    }

    function copyEntry(entry) {
        _runAction(["copyq", "select", entry.row.toString()], function(exitCode) {
            if (exitCode === 0) {
                ToastService.showInfo(entry.isImage ? I18n.tr("Image copied to clipboard") : I18n.tr("Copied to clipboard"));
                hide();
            } else {
                ToastService.showError(I18n.tr("Failed to copy entry"));
            }
        });
    }

    function deleteEntry(entry) {
        _runAction(["copyq", "remove", entry.row.toString()], function(exitCode) {
            if (exitCode === 0) {
                root._internalEntries = root._internalEntries.filter(e => e.id !== entry.id);
                root._mergeAndUpdate();
            }
        });
    }

    function deletePinnedEntry(entry) {
        confirmModal.show(
            I18n.tr("Delete Saved Item?"),
            I18n.tr("This will permanently remove this saved clipboard item."),
            function() {
                _runAction(["copyq", "eval", "tab('Saved'); remove(" + entry.row + ")"], function(exitCode) {
                    if (exitCode === 0) {
                        ToastService.showInfo(I18n.tr("Saved item deleted"));
                        refresh();
                    }
                });
            },
            function() {}
        );
    }

    function pinEntry(entry) {
        _runAction(["copyq", "eval", "var t=str(read(" + entry.row + ")); tab('Saved'); add(t)"], function(exitCode) {
            if (exitCode === 0) {
                ToastService.showInfo(I18n.tr("Entry pinned"));
                refresh();
            } else {
                ToastService.showError(I18n.tr("Failed to pin entry"));
            }
        });
    }

    function unpinEntry(entry) {
        _runAction(["copyq", "eval", "tab('Saved'); remove(" + entry.row + ")"], function(exitCode) {
            if (exitCode === 0) {
                ToastService.showInfo(I18n.tr("Entry unpinned"));
                refresh();
            } else {
                ToastService.showError(I18n.tr("Failed to unpin entry"));
            }
        });
    }

    function clearAll() {
        const savedCount = pinnedCount;
        _runAction(["copyq", "eval", "var n=count(); for(var i=n-1;i>=0;i--) remove(i)"], function(exitCode) {
            if (exitCode === 0) {
                refresh();
                if (savedCount > 0) {
                    ToastService.showInfo(I18n.tr("History cleared. %1 pinned entries kept.").arg(savedCount));
                }
            }
        });
    }

    function pasteSelected() {
        if (!keyboardNavigationActive || unpinnedEntries.length === 0 || selectedIndex < 0 || selectedIndex >= unpinnedEntries.length) {
            return;
        }
        const entry = unpinnedEntries[selectedIndex];
        _runAction(["copyq", "select", entry.row.toString()], function(exitCode) {
            if (exitCode === 0) {
                hide();
                pasteTimer.start();
            }
        });
    }

    function getEntryPreview(entry) {
        return entry.preview || "";
    }

    function getEntryType(entry) {
        if (entry.isImage) return "image";
        if (entry.size > _longTextThreshold) return "long_text";
        return "text";
    }

    function hashedPinnedEntry(entryHash) {
        return false;
    }

    function _runAction(cmd, callback) {
        if (actionProcess.running) {
            console.warn("CopyQClipboard: Action already in progress, queuing ignored");
            return;
        }
        actionProcess.command = cmd;
        actionProcess._callback = callback;
        actionProcess.running = true;
    }

    Component.onCompleted: {
        refresh();
    }
}
