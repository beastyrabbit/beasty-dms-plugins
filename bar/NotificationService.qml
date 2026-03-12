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

    property bool stackByApp: true
    property var _expandedApps: ({})

    property var displayNotifications: {
        var notifs = NotificationStore.notifications
        var stacked = root.stackByApp
        var expanded = root._expandedApps

        if (!stacked) return notifs

        var groups = {}
        var groupOrder = []
        for (var i = 0; i < notifs.length; i++) {
            var n = notifs[i]
            var key = n.desktopEntry || n.appName || ""
            if (!groups[key]) {
                groups[key] = []
                groupOrder.push(key)
            }
            groups[key].push(n)
        }

        var result = []
        for (var g = 0; g < groupOrder.length; g++) {
            var gKey = groupOrder[g]
            var items = groups[gKey]
            if (items.length === 1) {
                result.push(items[0])
            } else if (expanded[gKey]) {
                result.push({
                    _isGroupHeader: true,
                    appKey: gKey,
                    appName: items[0].appName || gKey,
                    appIcon: items[0].appIcon,
                    desktopEntry: items[0].desktopEntry,
                    image: items[0].image,
                    count: items.length,
                    time: items[0].time,
                    id: "header_" + gKey
                })
                for (var j = 0; j < items.length; j++) {
                    result.push(items[j])
                }
            } else {
                result.push({
                    _isStack: true,
                    appKey: gKey,
                    appName: items[0].appName || gKey,
                    appIcon: items[0].appIcon,
                    desktopEntry: items[0].desktopEntry,
                    image: items[0].image,
                    count: items.length,
                    summary: items[0].summary,
                    body: items[0].body,
                    time: items[0].time,
                    id: "stack_" + gKey
                })
            }
        }
        return result
    }

    readonly property int maxVisible: 10
    property bool _ready: false
    property string hoveredPopupId: ""
    property var _pendingRemovals: []

    onHoveredPopupIdChanged: {
        if (hoveredPopupId === "" && _pendingRemovals.length > 0) {
            // Sort pending by position in list — bottom (highest index) first
            var popups = root.popupNotifications
            root._pendingRemovals = root._pendingRemovals.slice().sort(function(a, b) {
                var idxA = -1, idxB = -1
                for (var i = 0; i < popups.length; i++) {
                    if (String(popups[i].id) === a) idxA = i
                    if (String(popups[i].id) === b) idxB = i
                }
                return idxB - idxA
            })
            cascadeTimer.start()
        } else if (hoveredPopupId !== "") {
            cascadeTimer.stop()
        }
    }

    Timer {
        id: cascadeTimer
        interval: 750
        repeat: true
        onTriggered: {
            if (root._pendingRemovals.length > 0) {
                var pending = root._pendingRemovals.slice()
                var nid = pending.shift()
                root._pendingRemovals = pending
                root.removePopupById(nid)
            } else {
                cascadeTimer.stop()
            }
        }
    }

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

                // Keep hovered item at its original position
                if (root.hoveredPopupId !== "") {
                    var hid = root.hoveredPopupId
                    for (var i = 2; i < popups.length; i++) {
                        if (String(popups[i].id) === hid) {
                            var temp = popups[i]
                            popups[i] = popups[i - 1]
                            popups[i - 1] = temp
                            break
                        }
                    }
                }

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

    // Discard queued popup notifications older than 20s
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

        // If any popup is hovered, queue this removal for later
        if (root.hoveredPopupId !== "") {
            var pending = root._pendingRemovals.slice()
            if (pending.indexOf(nid) === -1) pending.push(nid)
            root._pendingRemovals = pending
            return
        }

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

    function dismissAppStack(appKey) {
        var notifs = NotificationStore.notifications
        for (var i = notifs.length - 1; i >= 0; i--) {
            var n = notifs[i]
            var key = n.desktopEntry || n.appName || ""
            if (key === appKey) {
                root.removePopupById(n.id)
                var ids = root._unreadIds
                delete ids[n.id]
                root._unreadIds = ids
                NotificationStore.remove(n.id)
                // Dismiss from server
                for (var j = server.trackedNotifications.count - 1; j >= 0; j--) {
                    if (String(server.trackedNotifications.get(j).id) === String(n.id)) {
                        server.trackedNotifications.get(j).dismiss()
                        break
                    }
                }
            }
        }
        root._recountUnread()
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

    function toggleStacking() {
        root.stackByApp = !root.stackByApp
        root._expandedApps = ({})
    }

    function toggleAppExpanded(appKey) {
        var e = root._expandedApps
        if (e[appKey]) {
            delete e[appKey]
        } else {
            e[appKey] = true
        }
        root._expandedApps = e
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
