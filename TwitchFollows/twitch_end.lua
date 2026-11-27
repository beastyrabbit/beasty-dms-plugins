-- Closes a Twitch Follows window a while after its stream ended, unless the
-- user keeps it open. twitch_follows.py keeps mpv's input open once the
-- stream ends, so the window stays on the last picture with its chat, and
-- counts the stream's ends in user-data/twitch-stream-ended; 0 means it plays
-- again, as when the channel came back or a raid took the window over.
--
-- The countdown starts once mpv has played what it buffered, so a viewer
-- behind live (paused or rewound) sees the rest first. While it runs, the
-- OSD shows it, `k` keeps the window open, and uosc shows its controls with a
-- keep-open button (shown while user-data/twitch-closing is true). Kept open,
-- the window stays until it is closed; the next time the stream ends, the
-- countdown starts again.

local options = { close_seconds = 60 }
require("mp.options").read_options(options, "twitch_end")

local ENDED = "user-data/twitch-stream-ended"
local CLOSING = "user-data/twitch-closing"
local KEY = "k"
local BINDING = "twitch-keep-open"
-- Less than this left to play counts as played out.
local LEFT_SECONDS = 1

-- Waits for the buffer to play out, then counts down.
local waiting = nil
local timer = nil
local remaining = 0
-- The end whose countdown runs or ran.
local counted = 0

local function controls_shown(shown)
	mp.commandv("script-message-to", "uosc", "set-min-visibility", shown and "1" or "0", "controls")
end

local function stop()
	if waiting then
		waiting:kill()
		waiting = nil
	end
	if timer then
		timer:kill()
		timer = nil
		mp.remove_key_binding(BINDING)
		mp.set_property_native(CLOSING, false)
		controls_shown(false)
	end
end

local function tick()
	if remaining <= 0 then
		mp.command("quit")
		return
	end
	mp.osd_message(string.format("Stream ended. Closing in %d s, k keeps it open", remaining), 1.5)
	remaining = remaining - 1
end

local function keep_open()
	if timer then
		stop()
		mp.osd_message("Stream ended. The window stays open", 4)
	end
end

local function count_down()
	remaining = options.close_seconds
	mp.set_property_native(CLOSING, true)
	mp.add_forced_key_binding(KEY, BINDING, keep_open)
	controls_shown(true)
	timer = mp.add_periodic_timer(1, tick)
	tick()
end

local function played_out()
	local left = mp.get_property_number("demuxer-cache-duration")
	return left == nil or left < LEFT_SECONDS or mp.get_property_bool("paused-for-cache", false)
end

mp.observe_property(ENDED, "native", function(_, value)
	local ended = tonumber(value) or 0
	if ended > 0 and ended ~= counted then
		counted = ended
		stop()
		waiting = mp.add_periodic_timer(0.5, function()
			if played_out() then
				waiting:kill()
				waiting = nil
				count_down()
			end
		end)
	elseif ended == 0 then
		stop()
	end
end)

mp.register_script_message(BINDING, keep_open)
