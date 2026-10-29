import QtQuick
import Quickshell
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins
import qs.Widgets

PluginComponent {
    id: root

    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("twitch_follows.py"))
    readonly property int refreshSeconds: parseInt(pluginData.refreshSeconds ?? "120") || 120
    readonly property bool showOffline: pluginData.showOffline ?? true
    readonly property string quality: pluginData.quality || "best"
    readonly property bool chatOverlay: pluginData.chatOverlay ?? true
    readonly property string volume: pluginData.volume || "25"

    // Shared by every bar instance (one per screen), so only one fetch runs per interval.
    readonly property var follows: followsVar.value
    readonly property var liveChannels: follows.live ?? []
    readonly property var offlineChannels: follows.offline ?? []
    readonly property bool hasError: follows.ok === false
    readonly property bool loaded: follows.ok !== undefined
    property bool fetching: false
    readonly property string fetchId: "twitchFollows.list." + Math.random().toString(36).slice(2)

    readonly property var rows: {
        const result = [];
        if (liveChannels.length > 0)
            result.push({
                "kind": "header",
                "text": "Live · " + liveChannels.length
            });
        for (const channel of liveChannels)
            result.push(Object.assign({
                "kind": "live"
            }, channel));
        if (showOffline && offlineChannels.length > 0) {
            result.push({
                "kind": "header",
                "text": "Offline · " + offlineChannels.length
            });
            for (const channel of offlineChannels)
                result.push(Object.assign({
                    "kind": "offline"
                }, channel));
        }
        return result;
    }

    function refresh(force) {
        const age = Date.now() - (follows.fetchedAt ?? 0);
        if (fetching || (!force && age < refreshSeconds * 800))
            return;
        fetching = true;
        // Stamp the shared state first so the other screens' widgets skip this round.
        followsVar.set(Object.assign({}, follows, {
            "fetchedAt": Date.now()
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
            result.fetchedAt = Date.now();
            followsVar.set(result);
        }, 0, 30000, root);
    }

    // dms.service stops its whole cgroup on restart, which would close open
    // streams; a separate scope keeps players and chat running across restarts.
    function launch(args) {
        Quickshell.execDetached(["systemd-run", "--user", "--scope", "--quiet", "--collect", "python3", helperPath].concat(args));
    }

    function watch(login) {
        const args = ["--watch", login, "--quality", quality, "--volume", volume];
        if (!chatOverlay)
            args.push("--no-chat-overlay");
        launch(args);
    }

    function openChat(login) {
        launch(["--chat", login]);
    }

    popoutWidth: 340
    popoutHeight: 520
    Component.onDestruction: Proc.release(fetchId)

    PluginGlobalVar {
        id: followsVar

        varName: "follows"
        defaultValue: ({})
    }

    // pluginId is injected after creation; the shared state can't be written before it.
    Timer {
        interval: root.refreshSeconds * 1000
        repeat: true
        running: root.pluginId !== ""
        triggeredOnStart: true
        onTriggered: root.refresh(false)
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

                width: parent.width
                height: root.popoutHeight - popout.headerHeight - popout.detailsHeight - Theme.spacingL
                clip: true
                spacing: 2
                model: root.rows

                delegate: Item {
                    id: row

                    required property var modelData
                    readonly property bool isHeader: modelData.kind === "header"
                    readonly property bool isLive: modelData.kind === "live"

                    width: channelList.width
                    height: isHeader ? 28 : 40

                    StyledText {
                        visible: row.isHeader
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingS
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: Theme.spacingXS
                        text: row.modelData.text ?? ""
                        font.pixelSize: Theme.fontSizeSmall
                        font.weight: Font.Medium
                        color: Theme.surfaceVariantText
                    }

                    Rectangle {
                        visible: !row.isHeader
                        anchors.fill: parent
                        radius: Theme.cornerRadius
                        color: rowHover.hovered ? Theme.surfaceTextHover : "transparent"

                        HoverHandler {
                            id: rowHover
                        }

                        DCircularImage {
                            id: avatar

                            width: 30
                            height: 30
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spacingXS
                            anchors.verticalCenter: parent.verticalCenter
                            imageSource: row.modelData.avatar ?? ""
                            fallbackText: (row.modelData.name ?? "?").charAt(0).toUpperCase()
                            ringWidth: row.isLive ? 2 : 0
                            ringColor: "#eb0400"
                            opacity: row.isLive ? 1 : 0.5
                        }

                        Column {
                            anchors.left: avatar.right
                            anchors.leftMargin: Theme.spacingS
                            anchors.right: actions.left
                            anchors.rightMargin: Theme.spacingS
                            anchors.verticalCenter: parent.verticalCenter

                            StyledText {
                                width: parent.width
                                wrapMode: Text.NoWrap
                                text: row.modelData.name ?? ""
                                font.pixelSize: Theme.fontSizeMedium
                                color: row.isLive ? Theme.surfaceText : Theme.surfaceVariantText
                            }

                            StyledText {
                                visible: row.isLive && text !== ""
                                width: parent.width
                                wrapMode: Text.NoWrap
                                text: row.modelData.title ?? ""
                                font.pixelSize: Theme.fontSizeSmall - 1
                                color: Theme.surfaceVariantText
                            }
                        }

                        Row {
                            id: actions

                            anchors.right: parent.right
                            anchors.rightMargin: Theme.spacingXS
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Theme.spacingXS

                            DIconButton {
                                visible: row.isLive
                                iconName: "play_arrow"
                                tooltipText: "Watch in mpv"
                                onClicked: root.watch(row.modelData.login)
                            }

                            DIconButton {
                                iconName: "chat"
                                tooltipText: "Open chat in Chatterino"
                                onClicked: root.openChat(row.modelData.login)
                            }
                        }
                    }
                }
            }
        }
    }
}
