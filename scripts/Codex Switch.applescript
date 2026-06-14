on run
	set currentStatus to my runSwitch("status")
	set pickedList to choose from list {"Switch", "Status", "Sessions", "Settings"} with title "Codex Switch" with prompt currentStatus default items {"Switch"}
	if pickedList is false then return
	set picked to item 1 of pickedList
	
	if picked is "Switch" then
		my openSwitcher()
	else if picked is "Settings" then
		my openSettings()
	else if picked is "Sessions" then
		my openSessions()
	else
		display dialog currentStatus buttons {"OK"} default button "OK" with title "Codex Switch"
	end if
end run

on openSwitcher()
	set targetMode to button returned of (display dialog "Choose a Codex mode" buttons {"Cancel", "Local custom", "Official OpenAI"} default button "Official OpenAI" cancel button "Cancel" with title "Codex Switch")
	if targetMode is "Local custom" then
		set resultText to my runSwitch("local") & return & return & "Restart Codex App so the UI refreshes to custom."
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch"
	else if targetMode is "Official OpenAI" then
		set resultText to my runSwitch("official") & return & return & "Restart Codex App and sign in again if needed."
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

on openSessions()
	set pickedList to choose from list {"Snapshot", "Rebuild Index", "List Recent"} with title "Codex Switch Sessions" with prompt "Session tools" default items {"Rebuild Index"}
	if pickedList is false then return
	set picked to item 1 of pickedList
	if picked is "Snapshot" then
		set resultText to my runSwitch("sessions snapshot")
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch Sessions"
	else if picked is "Rebuild Index" then
		set resultText to my runSwitch("sessions rebuild-index") & return & return & "Restart Codex App so the session list refreshes."
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch Sessions"
	else if picked is "List Recent" then
		set resultText to my runSwitch("sessions list")
		display dialog resultText buttons {"OK"} default button "OK" with title "Codex Switch Sessions"
	end if
end openSessions

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
		set switchTool to my switchToolCommand()
		if commandName contains " " then
			set shellCommand to switchTool & space & commandName
		else
			set shellCommand to switchTool & space & quoted form of commandName
		end if
		return do shell script shellCommand
	on error errorMessage number errorNumber
		return "Failed (" & errorNumber & "):" & return & errorMessage
	end try
end runSwitch

on switchToolCommand()
	set installedTool to (POSIX path of (path to home folder)) & ".local/bin/codex-switch"
	if my fileExists(installedTool) then return quoted form of installedTool
	
	set appPath to POSIX path of (path to me)
	set bundledCli to appPath & "Contents/Resources/codex-switch/src/codex_switch/cli.py"
	if my fileExists(bundledCli) then return "/usr/bin/env python3 " & quoted form of bundledCli
	
	error "codex-switch CLI was not found. Reinstall the app or run scripts/install.sh."
end switchToolCommand

on fileExists(posixPath)
	try
		do shell script "test -f " & quoted form of posixPath
		return true
	on error
		return false
	end try
end fileExists
