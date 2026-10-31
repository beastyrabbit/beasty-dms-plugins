-- Lets the uosc buttons of a Twitch Follows window show and change its
-- settings. mpv reports user-data strings JSON-quoted ("yes" with the quotes),
-- so a uosc button bound to user-data never matches its own values. Bound to
-- `name@twitch_buttons` instead, uosc sends clicks here as `set <name> <value>`
-- and shows whatever value this script reports back. The values themselves
-- live in user-data/<name>, where twitch_follows.py and chat_overlay.py read
-- and write them.

local names = { "twitch-chat-visible", "twitch-max-quality", "twitch-audio-only" }
local known = {}

for _, name in ipairs(names) do
	known[name] = true
	mp.observe_property("user-data/" .. name, "native", function(_, value)
		if value ~= nil then
			mp.commandv("script-message-to", "uosc", "set", name, tostring(value))
		end
	end)
end

mp.register_script_message("set", function(name, value)
	if known[name] then
		mp.set_property_native("user-data/" .. name, value)
	end
end)
