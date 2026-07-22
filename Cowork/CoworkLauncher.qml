import QtQuick
import Quickshell

Item {
    id: root

    property var pluginService: null
    property string trigger: "cow"

    signal itemsChanged()

    readonly property string workspaceDirectory: (Quickshell.env("HOME") || "/home/beasty") + "/cowork"
    readonly property var providers: [
        {
            id: "codex",
            name: "Codex",
            command: "codex --yolo",
            icon: "material:code",
            keywords: "codex openai co"
        },
        {
            id: "claude",
            name: "Claude",
            command: "claude --dangerously-skip-permissions",
            icon: "material:psychology",
            keywords: "claude anthropic"
        },
        {
            id: "pi",
            name: "Pi",
            command: "pi",
            icon: "material:change_history",
            keywords: "pi"
        }
    ]

    function getItems(query) {
        const normalizedQuery = (query || "").trim().toLowerCase()
        const providerQuery = "ork".startsWith(normalizedQuery) ? "" : normalizedQuery
        const matchingProviders = providerQuery
            ? providers.filter(provider => provider.keywords.includes(providerQuery))
            : providers

        return matchingProviders.map(provider => ({
            name: provider.name,
            icon: provider.icon,
            comment: "Open " + provider.name + " in ~/cowork",
            action: "launch:" + provider.id,
            categories: ["Cowork"]
        }))
    }

    function executeItem(item) {
        if (!item || !item.action || !item.action.startsWith("launch:"))
            return

        const providerId = item.action.substring("launch:".length)
        const provider = providers.find(candidate => candidate.id === providerId)
        if (!provider)
            return

        Quickshell.execDetached([
            "kitty",
            "--class", "cowork",
            "--title", "Cowork · " + provider.name,
            "--directory", workspaceDirectory,
            "zsh", "-ic", "exec " + provider.command
        ])
    }
}
