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
    property var _creationTimes: ({})
    property int unreadCount: 0

    readonly property int maxVisible: 10

    readonly property alias tracked: server.trackedNotifications
    readonly property int count: tracked ? tracked.count : 0

    NotificationServer {
        id: server
        bodySupported: true
        bodyMarkupSupported: true
        actionsSupported: true
        imageSupported: true
        keepOnReload: true

        onNotification: notification => {
            notification.tracked = true
            root._markUnread(notification.id)
            // Record creation time
            var times = root._creationTimes
            times[notification.id] = Date.now()
            root._creationTimes = times

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
                var t = root._creationTimes[n.id]
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
        var t = root._creationTimes[notifId]
        if (!t) return 10000
        return Math.max(0, 10000 - (Date.now() - t))
    }

    // Remove popup by ID — promote queued item if available
    function removePopupById(notifId) {
        var times = root._creationTimes
        delete times[notifId]
        root._creationTimes = times

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
            times = root._creationTimes
            times[next.id] = Date.now()
            root._creationTimes = times
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
        root.removePopupById(notif.id)
        var ids = root._unreadIds
        delete ids[notif.id]
        root._unreadIds = ids
        root._recountUnread()
        notif.dismiss()
    }

    function dismissAll() {
        root.popupNotifications = []
        root._queue = []
        root._unreadIds = ({})
        root.unreadCount = 0
        for (var i = server.trackedNotifications.count - 1; i >= 0; i--) {
            server.trackedNotifications.get(i).dismiss()
        }
    }
}
