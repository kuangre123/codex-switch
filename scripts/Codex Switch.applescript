on run
	set needsSetupText to my runSwitch("needs-setup")
	if needsSetupText contains "yes" then
		set setupChoice to button returned of (display dialog "首次使用需要配置自定义 API 地址和 API Key。" & return & return & "默认地址：https://jp.icodeeasy.cc" buttons {"稍后", "去配置"} default button "去配置" cancel button "稍后" with title "Codex Switch")
		if setupChoice is "去配置" then
			my openFirstRunSetup()
			return
		end if
	end if
	
	my openMainMenu()
end run

on openMainMenu()
	set currentStatus to my runSwitch("status-zh")
	set pickedList to choose from list {"切换模式", "查看状态", "会话工具", "设置"} with title "Codex Switch" with prompt currentStatus default items {"切换模式"}
	if pickedList is false then return
	set picked to item 1 of pickedList
	
	if picked is "切换模式" then
		my openSwitcher()
	else if picked is "设置" then
		my openSettings()
	else if picked is "会话工具" then
		my openSessions()
	else
		display dialog currentStatus buttons {"好"} default button "好" with title "Codex Switch"
	end if
end openMainMenu

on openSwitcher()
	set targetMode to button returned of (display dialog "请选择 Codex 模式" buttons {"取消", "自定义 API", "官方 OpenAI"} default button "自定义 API" cancel button "取消" with title "Codex Switch")
	if targetMode is "自定义 API" then
		if my runSwitch("needs-setup") contains "yes" then
			set setupChoice to button returned of (display dialog "还没有配置 API Key，需要先填写自定义 API 信息。" buttons {"取消", "去配置"} default button "去配置" cancel button "取消" with title "Codex Switch")
			if setupChoice is "去配置" then my openFirstRunSetup()
		else
			set resultText to my runSwitch("local") & return & return & "请重启 Codex App，让界面刷新到自定义 API 模式。"
			display dialog resultText buttons {"好"} default button "好" with title "Codex Switch"
		end if
	else if targetMode is "官方 OpenAI" then
		set resultText to my runSwitch("official") & return & return & "请重启 Codex App；如果需要，重新登录官方账号。"
		display dialog resultText buttons {"好"} default button "好" with title "Codex Switch"
	end if
end openSwitcher

on openFirstRunSetup()
	set currentConfig to my runSwitch("config show")
	set defaultBaseUrl to my currentValue(currentConfig, "local_base_url")
	set defaultModel to my currentValue(currentConfig, "local_model")
	if defaultBaseUrl is "" then set defaultBaseUrl to "https://jp.icodeeasy.cc"
	if defaultModel is "" then set defaultModel to "gpt-5.5"
	
	set localBaseUrl to text returned of (display dialog "自定义 API 地址" default answer defaultBaseUrl buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "Codex Switch 首次配置")
	set localModel to text returned of (display dialog "模型名称" default answer defaultModel buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "Codex Switch 首次配置")
	set apiKey to text returned of (display dialog "API Key" default answer "" buttons {"取消", "保存并切换"} default button "保存并切换" cancel button "取消" with title "Codex Switch 首次配置" with hidden answer)
	
	set resultText to my runSwitch("local --base-url " & quoted form of localBaseUrl & " --model " & quoted form of localModel & " --api-key " & quoted form of apiKey)
	display dialog resultText & return & return & "已保存配置。请重启 Codex App，让界面刷新到自定义 API 模式。" buttons {"好"} default button "好" with title "Codex Switch"
end openFirstRunSetup

on openSettings()
	set currentConfig to my runSwitch("config show-zh")
	set rawConfig to my runSwitch("config show")
	set localBaseUrl to text returned of (display dialog currentConfig & return & return & "自定义 API 地址" default answer my currentValue(rawConfig, "local_base_url") buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "Codex Switch 设置")
	set localModel to text returned of (display dialog "自定义模型名称" default answer my currentValue(rawConfig, "local_model") buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "Codex Switch 设置")
	set officialModel to text returned of (display dialog "官方模型名称" default answer my currentValue(rawConfig, "official_model") buttons {"取消", "保存"} default button "保存" cancel button "取消" with title "Codex Switch 设置")
	
	set saveCommand to "config set --local-base-url " & quoted form of localBaseUrl & " --local-model " & quoted form of localModel & " --official-model " & quoted form of officialModel
	set resultText to my runSwitch(saveCommand)
	display dialog resultText buttons {"好"} default button "好" with title "Codex Switch 设置"
end openSettings

on openSessions()
	set pickedList to choose from list {"备份会话列表", "重建会话索引", "查看最近会话"} with title "Codex Switch 会话工具" with prompt "如果切换后会话列表不见了，优先用“重建会话索引”。" default items {"重建会话索引"}
	if pickedList is false then return
	set picked to item 1 of pickedList
	if picked is "备份会话列表" then
		set resultText to my runSwitch("sessions snapshot")
		display dialog resultText buttons {"好"} default button "好" with title "Codex Switch 会话工具"
	else if picked is "重建会话索引" then
		set resultText to my runSwitch("sessions rebuild-index") & return & return & "请重启 Codex App，让会话列表刷新。"
		display dialog resultText buttons {"好"} default button "好" with title "Codex Switch 会话工具"
	else if picked is "查看最近会话" then
		set resultText to my runSwitch("sessions list")
		display dialog resultText buttons {"好"} default button "好" with title "Codex Switch 会话工具"
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
		return "失败 (" & errorNumber & "):" & return & errorMessage
	end try
end runSwitch

on switchToolCommand()
	set installedTool to (POSIX path of (path to home folder)) & ".local/bin/codex-switch"
	if my fileExists(installedTool) then return quoted form of installedTool
	
	set appPath to POSIX path of (path to me)
	set bundledCli to appPath & "Contents/Resources/codex-switch/src/codex_switch/cli.py"
	if my fileExists(bundledCli) then return "/usr/bin/env python3 " & quoted form of bundledCli
	
	error "找不到 codex-switch 命令行工具，请重新安装 App 或运行 scripts/install.sh。"
end switchToolCommand

on fileExists(posixPath)
	try
		do shell script "test -f " & quoted form of posixPath
		return true
	on error
		return false
	end try
end fileExists

