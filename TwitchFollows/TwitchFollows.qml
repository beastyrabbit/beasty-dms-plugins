import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins
import qs.Widgets

PluginComponent {
    id: root

    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("twitch_follows.py"))
    readonly property int refreshSeconds: parseInt(pluginData.refreshSeconds ?? "120") || 120
    readonly property string quality: pluginData.quality || "best"
    readonly property bool chatInPlayer: pluginData.chatInPlayer ?? false
    readonly property string volume: String(pluginData.volume ?? "30")

    // Shared by every bar instance (one per screen), so only one fetch runs per interval.
    readonly property var follows: followsVar.value
    readonly property var liveChannels: follows.live ?? []
    readonly property bool hasError: follows.ok === false
    readonly property bool loaded: follows.ok !== undefined
    // After a failed fetch, as when the network is not up yet after boot, fetch
    // again in 10 s, doubling each time up to the normal interval.
    readonly property int nextFetchSeconds: {
        const failures = follows.failures ?? 0;
        return failures ? Math.min(10 * Math.pow(2, failures - 1), refreshSeconds) : refreshSeconds;
    }
    property bool fetching: false
    readonly property string fetchId: "twitchFollows.list." + Math.random().toString(36).slice(2)
    // Viewing groups (streams sharing one chat window), numbered in order.
    property var groups: []

    function groupOf(login) {
        return groups.findIndex(group => group.streams.some(stream => stream.login === login));
    }

    function refresh(force) {
        const now = Date.now();
        const age = now - (follows.fetchedAt ?? 0);
        // A fetch can outlast the 10 s retry, so at most one runs across all
        // screens; a little longer than the 30 s after which the helper is stopped.
        const running = now - (follows.fetchStartedAt ?? 0) < 35000;
        if (fetching || running || (!force && age < nextFetchSeconds * 800))
            return;
        fetching = true;
        // Stamp the shared state first so the other screens' widgets skip this round.
        followsVar.set(Object.assign({}, follows, {
            "fetchedAt": now,
            "fetchStartedAt": now
        }));
        Proc.runCommand(fetchId, ["python3", helperPath, "--list"], (output, exitCode) => {
            root.fetching = false;
            let result = null;
            try {
                result = JSON.parse(output.trim());
            } catch (error) {
                result = null;
            }
            if (exitCode !== 0 || !result)
                result = {
                    "ok": false,
                    "message": "The Twitch helper failed."
                };
            // Keep the last good list visible when a refresh fails.
            if (!result.ok && root.follows.ok)
                result = Object.assign({}, root.follows, {
                    "message": result.message
                });
            if (!result.ok || result.message)
                result.failures = (root.follows.failures ?? 0) + 1;
            result.fetchedAt = Date.now();
            result.fetchStartedAt = 0;
            followsVar.set(result);
        }, 0, 30000, root);
    }

    // dms.service stops its whole cgroup on restart, which would close open
    // streams; a separate scope keeps players and chat running across restarts.
    function launch(args) {
        Quickshell.execDetached(["systemd-run", "--user", "--scope", "--quiet", "--collect", "python3", helperPath].concat(args));
    }

    // Without a groupId the stream starts a new group.
    function watch(channel, muted, groupId) {
        const title = channel.title ? channel.name + " - " + channel.title : channel.name;
        const args = ["--watch", channel.login, "--quality", quality, "--volume", volume, "--media-title", title];
        if (muted)
            args.push("--muted");
        if (chatInPlayer)
            args.push("--chat-in-player");
        if (groupId)
            args.push("--group", groupId);
        launch(args);
    }

    popoutWidth: 440
    popoutHeight: 520
    Component.onDestruction: Proc.release(fetchId)

    PluginGlobalVar {
        id: followsVar

        varName: "follows"
        defaultValue: ({})
    }

    // pluginId is injected after creation; the shared state can't be written before it.
    Timer {
        // Changing the interval restarts the timer, so a failure retries soon.
        interval: root.nextFetchSeconds * 1000
        repeat: true
        running: root.pluginId !== ""
        triggeredOnStart: true
        onTriggered: root.refresh(false)
    }

    // The helper rewrites this file whenever a stream joins or leaves a group.
    FileView {
        id: groupsFile

        path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/twitch-follows-groups.json"
        printErrors: false
        onLoaded: {
            try {
                root.groups = JSON.parse(text()).groups ?? [];
            } catch (error) {}
        }
        onLoadFailed: root.groups = []
    }

    // The groups only show in the open list, so only poll while it is open.
    Timer {
        interval: 1000
        repeat: true
        running: root.interactionActive
        triggeredOnStart: true
        onTriggered: groupsFile.reload()
    }

    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS

            DIcon {
                name: "live_tv"
                size: root.iconSize
                color: root.liveChannels.length > 0 ? Theme.primary : Theme.surfaceVariantText
                anchors.verticalCenter: parent.verticalCenter
            }

            StyledText {
                text: root.loaded ? String(root.liveChannels.length) : "–"
                font.pixelSize: root.textSize
                color: root.hasError ? Theme.error : Theme.surfaceText
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: Theme.spacingXS

            DIcon {
                name: "live_tv"
                size: root.iconSize
                color: root.liveChannels.length > 0 ? Theme.primary : Theme.surfaceVariantText
                anchors.horizontalCenter: parent.horizontalCenter
            }

            StyledText {
                text: root.loaded ? String(root.liveChannels.length) : "–"
                font.pixelSize: root.textSize
                color: root.hasError ? Theme.error : Theme.surfaceText
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
    }

    popoutContent: Component {
        PopoutComponent {
            id: popout

            headerText: "Twitch"
            detailsText: {
                if (root.follows.message)
                    return root.follows.message;
                if (!root.loaded)
                    return "Loading followed channels…";
                if (root.liveChannels.length === 0)
                    return "Nobody you follow is live right now.";
                return root.liveChannels.length + " live · logged in as " + root.follows.user;
            }

            headerActions: Component {
                DIconButton {
                    iconName: "refresh"
                    tooltipText: "Refresh"
                    enabled: !root.fetching
                    onClicked: root.refresh(true)
                }
            }

            DListView {
                id: channelList

                // As tall as its rows, up to the room the screen leaves below the bar:
                // only then does it scroll (and show a scrollbar).
                readonly property real room: {
                    const screenRoom = popout.parentPopout?.maxBodyHeight ?? 0;
                    const total = screenRoom > 0 ? screenRoom : root.popoutHeight;
                    return total - popout.headerHeight - popout.detailsHeight - PopoutMetrics.contentPadding * 2;
                }

                width: parent.width
                height: Math.min(contentHeight, room)
                interactive: contentHeight > room
                clip: true
                spacing: 2
                model: root.liveChannels

                delegate: Rectangle {
                    id: row

                    required property var modelData
                    readonly property int groupIndex: root.groupOf(modelData.login)

                    width: channelList.width
                    height: 58
                    radius: Theme.cornerRadius
                    color: rowHover.hovered ? Theme.surfaceTextHover : "transparent"

                    HoverHandler {
                        id: rowHover
                    }

                    // Below the group buttons, so they keep their own clicks.
                    MouseArea {
                        anchors.fill: parent
                        enabled: row.groupIndex < 0
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        cursorShape: Qt.PointingHandCursor
                        onClicked: mouse => root.watch(row.modelData, mouse.button === Qt.RightButton, "")
                    }

                    DCircularImage {
                        id: avatar

                        width: 48
                        height: 48
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingXS
                        anchors.verticalCenter: parent.verticalCenter
                        imageSource: row.modelData.avatar ?? ""
                        fallbackText: (row.modelData.name ?? "?").charAt(0).toUpperCase()
                        ringWidth: 0
                    }

                    Column {
                        anchors.left: avatar.right
                        anchors.leftMargin: Theme.spacingM
                        anchors.right: actions.left
                        anchors.rightMargin: Theme.spacingS
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2

                        StyledText {
                            width: parent.width
                            wrapMode: Text.NoWrap
                            text: row.modelData.name ?? ""
                            font.pixelSize: Theme.fontSizeLarge
                            font.weight: Font.Medium
                            color: Theme.surfaceText
                        }

                        // Fixed height and visibility, so swapping game and title on
                        // hover never moves the name: titles often hold taller glyphs.
                        StyledText {
                            visible: !!(row.modelData.game || row.modelData.title)
                            width: parent.width
                            height: Math.ceil(font.pixelSize * 1.4)
                            verticalAlignment: Text.AlignVCenter
                            wrapMode: Text.NoWrap
                            // The game by default; hovering reveals the stream title.
                            text: (rowHover.hovered ? row.modelData.title : row.modelData.game) ?? ""
                            font.pixelSize: Theme.fontSizeMedium - 1
                            color: Theme.surfaceVariantText
                        }
                    }

                    Row {
                        id: actions

                        anchors.right: parent.right
                        anchors.rightMargin: Theme.spacingXS
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingXS

                        DBadge {
                            visible: row.groupIndex >= 0
                            anchors.verticalCenter: parent.verticalCenter
                            text: String(row.groupIndex + 1)
                            tooltipText: "Watching in group " + (row.groupIndex + 1)
                        }

                        Repeater {
                            model: row.groupIndex < 0 ? root.groups : []

                            delegate: DButton {
                                id: joinButton

                                required property var modelData
                                required property int index

                                visible: modelData.shared
                                anchors.verticalCenter: parent.verticalCenter
                                text: String(index + 1)
                                buttonHeight: Theme.iconSize + Theme.spacingS * 2
                                minimumWidth: buttonHeight
                                tooltipText: "Add to group " + (index + 1) + " with " + modelData.streams.map(stream => stream.login).join(", ") + " (right-click: muted)"
                                onClicked: root.watch(row.modelData, false, modelData.id)

                                TapHandler {
                                    acceptedButtons: Qt.RightButton
                                    onTapped: root.watch(row.modelData, true, joinButton.modelData.id)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
