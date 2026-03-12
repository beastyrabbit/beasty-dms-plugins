pragma Singleton

import Quickshell
import QtQuick

Singleton {
    id: root

    // Catppuccin Mocha palette
    readonly property color base: "#35353e"
    readonly property color text: "#cdd6f4"
    readonly property color subtext0: "#a6adc8"
    readonly property color surface0: "#65687a"
    readonly property color crust: "#222228"

    // Neutral dark greys (for panels, cards, surfaces)
    readonly property color panelBg: "#2b2b32"
    readonly property color cardBg: "#3a3a44"
    readonly property color cardHover: "#454550"
    readonly property color border: "#505058"
    readonly property color dimText: "#888888"

    // Animation durations
    readonly property int animFast: 150
    readonly property int animNormal: 250
    readonly property int animSlow: 400
    readonly property color rosewater: "#eba0ac"
    readonly property color flamingo: "#f2cdcd"
    readonly property color pink: "#f5c2e7"
    readonly property color mauve: "#cba6f7"
    readonly property color red: "#f38ba8"
    readonly property color maroon: "#eba0ac"
    readonly property color peach: "#fab387"
    readonly property color yellow: "#f9e2af"
    readonly property color green: "#a6e3a1"
    readonly property color teal: "#94e2d5"
    readonly property color sky: "#89dceb"
    readonly property color sapphire: "#74c7ec"
    readonly property color blue: "#89b4fa"
    readonly property color lavender: "#b4befe"
    readonly property color transparent_: "transparent"

    readonly property string fontFamily: "OpenDyslexic Nerd Font"
    readonly property int fontSize: 16
    readonly property int barHeight: 38
    readonly property int pillRadius: 10
    readonly property int pillPaddingH: 10
    readonly property int pillMarginV: 3
    readonly property int pillMarginTop: 5
    readonly property int pillHeight: barHeight - pillMarginTop - pillMarginV
}
