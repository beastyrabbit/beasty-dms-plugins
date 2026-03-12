pragma Singleton

import Quickshell
import Quickshell.Io
import QtQuick

Singleton {
    id: root

    // The panel model — array of {id, time, summary, body, appIcon, appName, image, desktopEntry}
    property var notifications: []
    property int count: notifications.length
    property bool ready: false

    readonly property string _storePath: `${Quickshell.env("XDG_STATE_HOME") || `${Quickshell.env("HOME")}/.local/state`}/quickshell-bar/notif-times.json`

    FileView {
        id: storage
        path: root._storePath
        onLoaded: {
            try {
                var data = JSON.parse(text())
                root.notifications = Array.isArray(data) ? data : []
            } catch(e) {
                root.notifications = []
            }
            root.ready = true
        }
        onLoadFailed: err => {
            if (err === FileViewError.FileNotFound)
                setText("[]")
            root.ready = true
        }
    }

    function _save() {
        storage.setText(JSON.stringify(root.notifications))
    }

    // Add a notification record (skips if id already exists)
    function add(notif) {
        var sid = String(notif.id)
        for (var i = 0; i < root.notifications.length; i++) {
            if (String(root.notifications[i].id) === sid) return
        }
        var record = {
            id: sid,
            time: Date.now(),
            summary: notif.summary || "",
            body: notif.body || "",
            appIcon: notif.appIcon || "",
            appName: notif.appName || "",
            image: notif.image || "",
            desktopEntry: notif.desktopEntry || ""
        }
        var list = root.notifications.slice()
        list.unshift(record)
        root.notifications = list
        _save()
    }

    // Get creation timestamp for a notification (0 if unknown)
    function getTime(notifId) {
        var sid = String(notifId)
        for (var i = 0; i < root.notifications.length; i++) {
            if (String(root.notifications[i].id) === sid)
                return root.notifications[i].time
        }
        return 0
    }

    // Get a notification record by index
    function get(index) {
        return root.notifications[index] || null
    }

    // Remove a single notification by id
    function remove(notifId) {
        var sid = String(notifId)
        var list = root.notifications.filter(function(n) {
            return String(n.id) !== sid
        })
        if (list.length !== root.notifications.length) {
            root.notifications = list
            _save()
        }
    }

    // Clear all stored notifications
    function removeAll() {
        root.notifications = []
        _save()
    }

    // Overwrite a notification's time (used for popup queue promotion)
    function setTime(notifId, timestamp) {
        var sid = String(notifId)
        var list = root.notifications.slice()
        for (var i = 0; i < list.length; i++) {
            if (String(list[i].id) === sid) {
                list[i].time = timestamp
                root.notifications = list
                _save()
                return
            }
        }
    }
}
