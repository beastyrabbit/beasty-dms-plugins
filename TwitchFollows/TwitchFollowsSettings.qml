import QtQuick
import Quickshell
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginSettings {
    id: root

    pluginId: "twitchFollows"

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

    DButton {
        text: "Log in to Twitch"
        // A notification shows the code; Twitch's page to confirm it opens in the browser.
        // The field saves only on Enter or focus loss, so a pasted ID is saved here first;
        // an empty or malformed one gets a notification from the script.
        onClicked: {
            clientIdSetting.commit();
            Quickshell.execDetached(["systemd-run", "--user", "--scope", "--quiet", "--collect", "python3", Paths.strip(Qt.resolvedUrl("twitch_follows.py")), "--raid-login", clientIdSetting.value.trim()]);
        }
    }
}
