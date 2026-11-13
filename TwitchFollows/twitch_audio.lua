-- Audio profiles for a Twitch Follows window. The uosc audio button cycles
-- user-data/twitch-audio-profile (through twitch_buttons.lua); this script
-- puts the profile's filters into mpv's audio chain under one label, so other
-- audio filters stay as they are. mpv keeps the filters and this script
-- across a raid's loadfile, so the profile stays with the window.
--
-- Twitch sends one mixed stereo track, so every profile works on the whole
-- mix: none can pick out the streamer's voice.

local PROPERTY = "user-data/twitch-audio-profile"
local LABEL = "twitch_audio"
-- Keeps peaks about 1 dB under full scale after a profile adds gain. Its
-- lookahead (the attack, 5 ms) is all the delay a profile adds; latency=true
-- lets mpv keep the picture in sync with it.
local LIMITER = "alimiter=limit=0.89:attack=5:release=80:level=false:latency=true"

-- Auto loudness: ebur128 measures the stream's short-term loudness (the last
-- 3 seconds) and a volume filter after it moves slowly towards TARGET_LUFS.
-- Nothing waits for the measurement, so it adds no delay.
local TARGET_LUFS = -18
local MAX_GAIN_DB = 12
-- Quieter than this is a pause or silence, which shouldn't pull the gain up.
local SILENCE_LUFS = -45
-- How quickly the estimate follows the measurement, and how fast the gain may
-- move: down quickly when a stream gets loud, up slowly.
local LOUDNESS_SECONDS = 8
local FALL_DB_PER_SECOND = 6
local RISE_DB_PER_SECOND = 1.5
local TICK_SECONDS = 0.25

-- The button's values, in its order: off, compressor, clarity, loudness, mono.
local titles = {
	off = "Off",
	compressor = "Compressor",
	clarity = "Clarity",
	loudness = "Auto loudness",
	mono = "Mono",
}

local gain_db = 0
local loudness = nil

local function graph(profile)
	if profile == "compressor" then
		-- Turns loud parts down and quiet ones up, like FrankerFaceZ's audio
		-- compressor: 4:1 above -18 dBFS, 6 dB makeup gain.
		return "acompressor=threshold=0.125:ratio=4:attack=5:release=250:makeup=2"
			.. ":knee=2.82843:link=maximum:detection=rms,"
			.. LIMITER
	elseif profile == "clarity" then
		-- Cuts rumble and lifts the range speech sits in, 3 dB down first so
		-- the boost has room.
		return "volume=0.7071,highpass=f=90:p=2,equalizer=f=2500:t=q:w=1:g=3," .. LIMITER
	elseif profile == "loudness" then
		return "ebur128=metadata=1:framelog=verbose,volume=volume="
			.. string.format("%.2f", gain_db)
			.. "dB,"
			.. LIMITER
	elseif profile == "mono" then
		return "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
	end
	return nil
end

local function present()
	for _, filter in ipairs(mp.get_property_native("af", {})) do
		if filter.label == LABEL then
			return true
		end
	end
	return false
end

-- Adding under a label that is there already replaces that filter in place.
-- A graph mpv can't build leaves the chain as it was, so the old profile
-- keeps playing.
local function apply(profile)
	if profile == "off" then
		if present() then
			mp.command_native({ "af", "remove", "@" .. LABEL })
		end
		return true
	end
	local filters = graph(profile)
	if filters == nil then
		return false
	end
	local _, err = mp.command_native({ "af", "add", "@" .. LABEL .. ":lavfi=[" .. filters .. "]" })
	return err == nil
end

local function tick()
	if mp.get_property_bool("core-idle", true) then
		return
	end
	local metadata = mp.get_property_native("af-metadata/" .. LABEL)
	local short_term = metadata and tonumber(metadata["lavfi.r128.S"])
	if short_term and short_term > SILENCE_LUFS then
		if loudness == nil then
			loudness = short_term
		else
			loudness = loudness + (short_term - loudness) * TICK_SECONDS / LOUDNESS_SECONDS
		end
	end
	if loudness ~= nil then
		local wanted = math.max(-MAX_GAIN_DB, math.min(MAX_GAIN_DB, TARGET_LUFS - loudness))
		local step = wanted < gain_db and FALL_DB_PER_SECOND or RISE_DB_PER_SECOND
		step = step * TICK_SECONDS
		gain_db = gain_db + math.max(-step, math.min(step, wanted - gain_db))
	end
	-- Sent every tick, also while a new channel is still being measured: mpv
	-- rebuilds the filters on loadfile and when the audio format changes,
	-- which puts the volume back to where the profile started it.
	mp.command_native({ "af-command", LABEL, "volume", string.format("%.2fdB", gain_db), "volume" })
end

local timer = mp.add_periodic_timer(TICK_SECONDS, tick)
timer:kill()

-- A raid brings another channel with its own loudness: measure it afresh and
-- start from the gain the last one had.
mp.register_event("file-loaded", function()
	loudness = nil
end)

local current = "off"

mp.observe_property(PROPERTY, "native", function(_, value)
	if value == nil then
		mp.set_property_native(PROPERTY, current)
		return
	end
	if value == current then
		return
	end
	if titles[value] == nil or not apply(value) then
		mp.osd_message("Audio profile " .. tostring(value) .. " is not available")
		-- Back to the profile that is still playing; the button follows.
		mp.set_property_native(PROPERTY, current)
		return
	end
	current = value
	if value == "loudness" then
		loudness = nil
		timer:resume()
	else
		timer:kill()
	end
	mp.osd_message("Audio: " .. titles[value])
end)
