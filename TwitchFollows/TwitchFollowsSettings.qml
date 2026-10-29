import QtQuick
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
        description: "Falls back to the best available quality when the stream doesn't offer this one."
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
        defaultValue: "25"
        options: [{
            "label": "0% (muted)",
            "value": "0"
        }, {
            "label": "10%",
            "value": "10"
        }, {
            "label": "25% (default)",
            "value": "25"
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
        settingKey: "chatOverlay"
        label: "Show chat in the player"
        description: "Draw the channel's chat over the stream. Press C in mpv to hide or show it."
        defaultValue: true
    }
}
