on run
	set currentStatus to my runSwitch("status")
	set actionButtons to {"Switch", "Status", "Settings"}
	set picked to button returned of (display dialog currentStatus buttons actionButtons default button "Switch" with title "Codex Switch")
	
	if picked is "Switch" then
		my openSwitcher()
	else if picked is "Settings" then
		my openSettings()
	else
		display dialog currentStatus buttons {"OK"} default button "OK" with title "Codex Switch"
	end if
end run

on openSwitcher()
	set targetMode to button returned of (display dialog "Choose a Codex mode" buttons {"Cancel", "Local custom", "Official OpenAI"} default button "Official OpenAI" cancel button "Cancel" with title "Codex Switch")
	if targetMode is "Local custom" then
		set resultText to my runSwitch("local --migrate-latest --restart-codex")
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch"
	else if targetMode is "Official OpenAI" then
		set resultText to my runSwitch("official --migrate-latest --restart-codex")
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch"
	end if
end openSwitcher

on openSettings()
	set currentConfig to my runSwitch("config show")
	set localBaseUrl to text returned of (display dialog currentConfig & return & return & "Local API base URL" default answer my currentValue(currentConfig, "local_base_url") buttons {"Cancel", "Next"} default button "Next" cancel button "Cancel" with title "Codex Switch Settings")
	set localModel to text returned of (display dialog "Local model name" default answer my currentValue(currentConfig, "local_model") buttons {"Cancel", "Next"} default button "Next" cancel button "Cancel" with title "Codex Switch Settings")
	set officialModel to text returned of (display dialog "Official model name" default answer my currentValue(currentConfig, "official_model") buttons {"Cancel", "Save"} default button "Save" cancel button "Cancel" with title "Codex Switch Settings")
	
	set saveCommand to "config set --local-base-url " & quoted form of localBaseUrl & " --local-model " & quoted form of localModel & " --official-model " & quoted form of officialModel
	set resultText to my runSwitch(saveCommand)
	display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch Settings"
end openSettings

on currentValue(sourceText, keyName)
	set oldDelimiters to AppleScript's text item delimiters
	try
		set AppleScript's text item delimiters to return
		set linesList to text items of sourceText
		repeat with lineText in linesList
			set lineString to lineText as text
			if lineString starts with (keyName & ": ") then
				set AppleScript's text item delimiters to (keyName & ": ")
				set valueParts to text items of lineString
				set AppleScript's text item delimiters to oldDelimiters
				if (count of valueParts) is greater than 1 then return item 2 of valueParts
			end if
		end repeat
	end try
	set AppleScript's text item delimiters to oldDelimiters
	return ""
end currentValue

on runSwitch(commandName)
	try
		set switchTool to (POSIX path of (path to home folder)) & ".local/bin/codex-switch"
		if commandName contains " " then
			set shellCommand to quoted form of switchTool & space & commandName
		else
			set shellCommand to quoted form of switchTool & space & quoted form of commandName
		end if
		return do shell script shellCommand
	on error errorMessage number errorNumber
		return "Failed (" & errorNumber & "):" & return & errorMessage
	end try
end runSwitch
