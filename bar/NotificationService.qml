pragma Singleton

import Quickshell
import Quickshell.Services.Notifications
import QtQuick

Singleton {
    id: root

    property bool panelVisible: false
    property bool panelHovered: false
    property bool bellHovered: false
    property var popupNotifications: []
    property var _queue: []
    property var _unreadIds: ({})
    property int unreadCount: 0

    readonly property int maxVisible: 10
    property bool _ready: false

    // Skip popups during startup — server re-sends tracked notifications on reload.
    // Waits for store to load so dismissed notifications stay dismissed.
    Timer {
        id: startupTimer
        interval: 1000
        running: NotificationStore.ready
        onTriggered: root._ready = true
    }

    readonly property alias tracked: server.trackedNotifications

    property int tick: 0
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: root.tick++
    }

    NotificationServer {
        id: server
        bodySupported: true
        bodyMarkupSupported: true
        actionsSupported: true
        imageSupported: true
        keepOnReload: true

        onNotification: notification => {
            notification.tracked = true

            // During startup, server re-sends all tracked notifications (keepOnReload).
            // The store already has what it needs from the persisted file.
            // Only mark unread for notifications that still exist in the store.
            if (!root._ready) {
                if (NotificationStore.getTime(notification.id) > 0)
                    root._markUnread(notification.id)
                return
            }

            root._markUnread(notification.id)

            if (root.popupNotifications.length < root.maxVisible) {
                var popups = root.popupNotifications.slice()
                popups.unshift(notification)
                root.popupNotifications = popups
            } else {
                // Queue overflow
                var q = root._queue.slice()
                q.push(notification)
                root._queue = q
            }

            NotificationStore.add(notification)
        }
    }

    // Discard queued notifications older than 20s
    Timer {
        id: queueCleanup
        interval: 2000
        repeat: true
        running: root._queue.length > 0

        onTriggered: {
            var now = Date.now()
            var q = root._queue.filter(function(n) {
                var t = NotificationStore.getTime(n.id)
                return t && (now - t) < 20000
            })
            if (q.length !== root._queue.length)
                root._queue = q
        }
    }

    // Delay before closing panel when mouse leaves
    Timer {
        id: closeDelayTimer
        interval: 400
        onTriggered: {
            if (!root.panelHovered && !root.bellHovered)
                root.panelVisible = false
        }
    }

    function showPanel() {
        closeDelayTimer.stop()
        root.panelVisible = true
    }

    function scheduleClose() {
        closeDelayTimer.restart()
    }

    function togglePanel() {
        if (root.panelVisible) {
            root.panelVisible = false
            closeDelayTimer.stop()
        } else {
            showPanel()
        }
    }

    function _markUnread(notifId) {
        var ids = root._unreadIds
        ids[notifId] = true
        root._unreadIds = ids
        root._recountUnread()
    }

    function isUnread(notifId) {
        return !!root._unreadIds[notifId]
    }

    function markRead(notifId) {
        var ids = root._unreadIds
        delete ids[notifId]
        root._unreadIds = ids
        root._recountUnread()
    }

    function _recountUnread() {
        var n = 0
        var ids = root._unreadIds
        for (var k in ids) { if (ids[k]) n++ }
        root.unreadCount = n
    }

    function getRemainingMs(notifId) {
        var t = NotificationStore.getTime(notifId)
        if (!t) return 10000
        return Math.max(0, 10000 - (Date.now() - t))
    }

    // Remove popup by ID — promote queued item if available
    function removePopupById(notifId) {
        var nid = String(notifId)
        var popups = root.popupNotifications.filter(function(n) {
            return String(n.id) !== nid
        })

        // Promote from queue if there's space
        if (popups.length < root.maxVisible && root._queue.length > 0) {
            var q = root._queue.slice()
            var next = q.shift()
            root._queue = q
            // Reset creation time so it gets a fresh 10s
            NotificationStore.setTime(next.id, Date.now())
            popups.unshift(next)
        }

        root.popupNotifications = popups
    }

    // Popup expired naturally — stays tracked as unread in panel
    function removePopup(notif) {
        root.removePopupById(notif.id)
    }

    // User explicitly dismissed — remove from everything
    function dismissNotification(notif) {
        var nid = String(notif.id)
        root.removePopupById(nid)
        var ids = root._unreadIds
        delete ids[nid]
        root._unreadIds = ids
        root._recountUnread()
        NotificationStore.remove(nid)
        // Dismiss from server if it's a live notification
        if (typeof notif.dismiss === "function") {
            notif.dismiss()
        } else {
            // Find and dismiss from tracked server notifications
            for (var i = server.trackedNotifications.count - 1; i >= 0; i--) {
                if (String(server.trackedNotifications.get(i).id) === nid) {
                    server.trackedNotifications.get(i).dismiss()
                    break
                }
            }
        }
    }

    function formatAge(timestamp) {
        void root.tick
        if (!timestamp) return ""
        var sec = Math.floor((Date.now() - timestamp) / 1000)
        if (sec < 60) return "now"
        var min = Math.floor(sec / 60)
        if (min < 60) return min + " min"
        var hr = Math.floor(min / 60)
        if (hr < 24) return hr + " h"
        var days = Math.floor(hr / 24)
        if (days < 7) return days + " d"
        var weeks = Math.floor(days / 7)
        if (weeks < 4) return weeks + " w"
        var mo = Math.floor(days / 30)
        return mo + " mo"
    }

    function getTimeGroup(timestamp) {
        void root.tick
        if (!timestamp) return ""
        var now = new Date()
        var created = new Date(timestamp)
        var startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        var diffMs = startOfToday - created
        if (diffMs <= 0) return ""
        var diffDays = Math.ceil(diffMs / 86400000)
        if (diffDays <= 1) return "Yesterday"
        if (diffDays <= 7) return "Last week"
        if (diffDays <= 30) return "Last month"
        return "Older"
    }

    // Click notification: invoke default action or focus sending app
    function activateNotification(notif) {
        // Try default action first
        var actions = notif.actions
        if (actions) {
            for (var i = 0; i < actions.length; i++) {
                if (actions[i].identifier === "default") {
                    actions[i].invoke()
                    return
                }
            }
        }
        // Fallback: focus app window via niri
        var app = notif.desktopEntry || notif.appName || ""
        if (app.length > 0) {
            var appLower = app.toLowerCase()
            Quickshell.execDetached(["niri", "msg", "action", "focus-window", "--app-id", appLower])
        }
    }

    function dismissAll() {
        root.popupNotifications = []
        root._queue = []
        root._unreadIds = ({})
        root.unreadCount = 0
        for (var i = server.trackedNotifications.count - 1; i >= 0; i--) {
            server.trackedNotifications.get(i).dismiss()
        }
        NotificationStore.removeAll()
    }
}
