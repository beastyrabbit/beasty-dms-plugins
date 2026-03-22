import QtQuick
import qs.Common

QtObject {
    id: keyboardController

    required property var modal

    function reset() {
        modal.selectedIndex = 0;
        modal.keyboardNavigationActive = false;
        modal.showKeyboardHints = false;
    }

    function selectNext() {
        const entries = modal.activeTab === "saved" ? modal.pinnedEntries : modal.unpinnedEntries;
        if (!entries || entries.length === 0) {
            return;
        }
        modal.keyboardNavigationActive = true;
        modal.selectedIndex = Math.min(modal.selectedIndex + 1, entries.length - 1);
    }

    function selectPrevious() {
        const entries = modal.activeTab === "saved" ? modal.pinnedEntries : modal.unpinnedEntries;
        if (!entries || entries.length === 0) {
            return;
        }
        modal.keyboardNavigationActive = true;
        modal.selectedIndex = Math.max(modal.selectedIndex - 1, 0);
    }

    function copySelected() {
        const entries = modal.activeTab === "saved" ? modal.pinnedEntries : modal.unpinnedEntries;
        if (!entries || entries.length === 0 || modal.selectedIndex < 0 || modal.selectedIndex >= entries.length) {
            return;
        }
        const selectedEntry = entries[modal.selectedIndex];
        modal.copyEntry(selectedEntry);
    }

    function deleteSelected() {
        const entries = modal.activeTab === "saved" ? modal.pinnedEntries : modal.unpinnedEntries;
        if (!entries || entries.length === 0 || modal.selectedIndex < 0 || modal.selectedIndex >= entries.length) {
            return;
        }
        const selectedEntry = entries[modal.selectedIndex];
        if (modal.activeTab === "saved") {
            modal.deletePinnedEntry(selectedEntry);
        } else {
            modal.deleteEntry(selectedEntry);
        }
    }

    function handleKey(event) {
        switch (event.key) {
        case Qt.Key_Escape:
            if (modal.keyboardNavigationActive) {
                modal.keyboardNavigationActive = false;
            } else {
                modal.hide();
            }
            event.accepted = true;
            return;
        case Qt.Key_Down:
        case Qt.Key_Tab:
            if (!modal.keyboardNavigationActive) {
                modal.keyboardNavigationActive = true;
                modal.selectedIndex = 0;
            } else {
                selectNext();
            }
            event.accepted = true;
            return;
        case Qt.Key_Up:
        case Qt.Key_Backtab:
            if (!modal.keyboardNavigationActive) {
                modal.keyboardNavigationActive = true;
                modal.selectedIndex = 0;
            } else if (modal.selectedIndex === 0) {
                modal.keyboardNavigationActive = false;
            } else {
                selectPrevious();
            }
            event.accepted = true;
            return;
        case Qt.Key_F10:
            modal.showKeyboardHints = !modal.showKeyboardHints;
            event.accepted = true;
            return;
        }

        if (event.modifiers & Qt.ControlModifier) {
            switch (event.key) {
            case Qt.Key_N:
            case Qt.Key_J:
                if (!modal.keyboardNavigationActive) {
                    modal.keyboardNavigationActive = true;
                    modal.selectedIndex = 0;
                } else {
                    selectNext();
                }
                event.accepted = true;
                return;
            case Qt.Key_P:
            case Qt.Key_K:
                if (!modal.keyboardNavigationActive) {
                    modal.keyboardNavigationActive = true;
                    modal.selectedIndex = 0;
                } else if (modal.selectedIndex === 0) {
                    modal.keyboardNavigationActive = false;
                } else {
                    selectPrevious();
                }
                event.accepted = true;
                return;
            case Qt.Key_C:
                if (modal.keyboardNavigationActive) {
                    copySelected();
                    event.accepted = true;
                }
                return;
            }
        }

        if (event.modifiers & Qt.ShiftModifier) {
            switch (event.key) {
            case Qt.Key_Delete:
                modal.clearAll();
                modal.hide();
                event.accepted = true;
                return;
            case Qt.Key_Return:
            case Qt.Key_Enter:
                if (modal.keyboardNavigationActive) {
                    if (SettingsData.clipboardEnterToPaste) {
                        copySelected();
                    } else {
                        modal.pasteSelected();
                    }
                    event.accepted = true;
                }
                return;
            }
        }

        if (modal.keyboardNavigationActive) {
            switch (event.key) {
            case Qt.Key_Return:
            case Qt.Key_Enter:
                if (SettingsData.clipboardEnterToPaste) {
                    modal.pasteSelected();
                } else {
                    copySelected();
                }
                event.accepted = true;
                return;
            case Qt.Key_Delete:
                deleteSelected();
                event.accepted = true;
                return;
            }
        }
    }
}
