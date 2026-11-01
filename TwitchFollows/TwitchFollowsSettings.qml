import QtQuick
import Quickshell
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginSettings {
    id: root

    pluginId: "twitchFollows"

    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("twitch_follows.py"))
    readonly property string statusId: "twitchFollowsRaidStatus"
    property var raidStatus: ({})
    property bool polling: false
    property real loginStartedAt: 0

    function refreshStatus() {
        Proc.runCommand(statusId, ["python3", helperPath, "--raid-status"], (output, exitCode) => {
            try {
                raidStatus = JSON.parse(output.trim());
            } catch (error) {
                return;
            }
            // Also picks up a login started before these settings opened.
            polling = raidStatus.running || Date.now() - loginStartedAt <= 5000;
        }, 0, 10000, root);
    }

    function loginText() {
        const status = raidStatus;
        if (status.code)
            return "Enter the code " + status.code + " on twitch.tv/activate (the page opened in your browser).";
        if (status.running)
            return "Asking Twitch for a code…";
        const login = status.loggedIn ? "Logged in as " + (status.login || "you") + "." : "Not logged in.";
        return status.error ? "Login failed: " + status.error + " " + login : login;
    }

    StyledText {
        width: parent.width
        text: "Twitch Follows"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Theme.fontWeightMedium
        color: Theme.surfaceText
    }

    StyledText {
        width: parent.width
        text: "Uses the account you are logged in with in Chatterino. Streams play in mpv through streamlink."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    SelectionSetting {
        settingKey: "quality"
        label: "Stream quality"
        description: "The highest quality to use. If a channel doesn't offer it, the next lower one plays."
        defaultValue: "best"
        options: [{
            "label": "Best (default)",
            "value": "best"
        }, {
            "label": "1080p60",
            "value": "1080p60"
        }, {
            "label": "720p60",
            "value": "720p60"
        }, {
            "label": "480p",
            "value": "480p"
        }, {
            "label": "Audio only",
            "value": "audio_only"
        }]
    }

    SelectionSetting {
        settingKey: "volume"
        label: "Starting volume"
        description: "mpv volume when a stream opens."
        defaultValue: "30"
        options: [{
            "label": "0% (muted)",
            "value": "0"
        }, {
            "label": "10%",
            "value": "10"
        }, {
            "label": "30% (default)",
            "value": "30"
        }, {
            "label": "50%",
            "value": "50"
        }, {
            "label": "75%",
            "value": "75"
        }, {
            "label": "100%",
            "value": "100"
        }]
    }

    SelectionSetting {
        settingKey: "refreshSeconds"
        label: "Refresh interval"
        description: "How often to check which followed channels are live."
        defaultValue: "120"
        options: [{
            "label": "1 minute",
            "value": "60"
        }, {
            "label": "2 minutes (default)",
            "value": "120"
        }, {
            "label": "5 minutes",
            "value": "300"
        }]
    }

    ToggleSetting {
        settingKey: "chatInPlayer"
        label: "Start with chat in the player"
        description: "Open new streams with their chat drawn in mpv and the Chatterino window hidden. The chat button in mpv (or C) switches a whole group between the two."
        defaultValue: false
    }

    ToggleSetting {
        settingKey: "followRaids"
        label: "Follow raids"
        description: "When a channel you watch raids another, the raided channel takes over its window and chat tab. Needs the Twitch login below and uv."
        defaultValue: true
    }

    StringSetting {
        id: clientIdSetting

        settingKey: "raidClientId"
        label: "Twitch app client ID"
        description: "Following raids needs a Twitch app of its own: register one at dev.twitch.tv/console as a public client (any name, redirect URL http://localhost) and paste its client ID here."
        placeholder: "Client ID"
    }

    StyledText {
        width: parent.width
        text: root.loginText()
        font.pixelSize: Theme.fontSizeMedium
        color: root.raidStatus.error && !root.raidStatus.running ? Theme.error : Theme.surfaceText
        wrapMode: Text.WordWrap
    }

    DButton {
        text: root.raidStatus.loggedIn ? "Log in again" : "Log in to Twitch"
        enabled: !root.raidStatus.running
        // The code shows above and in a notification; Twitch's page to confirm it opens in the
        // browser. The field saves only on Enter or focus loss, so a pasted ID is saved here
        // first; the script says when it is empty or malformed.
        onClicked: {
            clientIdSetting.commit();
            Quickshell.execDetached(["systemd-run", "--user", "--scope", "--quiet", "--collect", "python3", root.helperPath, "--raid-login", clientIdSetting.value.trim()]);
            root.loginStartedAt = Date.now();
            root.polling = true;
        }
    }

    // Polled only while a login runs (and a few seconds after the click, before the
    // script has started); checked once whenever the settings show.
    Timer {
        interval: 1000
        repeat: true
        running: root.visible && root.polling
        triggeredOnStart: true
        onTriggered: root.refreshStatus()
    }

    onVisibleChanged: {
        if (visible)
            refreshStatus();
    }
    Component.onCompleted: refreshStatus()
    Component.onDestruction: Proc.release(statusId)
}
