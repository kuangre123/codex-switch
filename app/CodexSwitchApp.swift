import AppKit
import Foundation
import SwiftUI

enum AppLanguage: String, CaseIterable, Identifiable {
    case zh
    case en

    var id: String { rawValue }
}

enum ProviderMode: String, CaseIterable, Identifiable {
    case custom
    case official

    var id: String { rawValue }
}

enum SwitchTarget: String, CaseIterable, Identifiable {
    case codex
    case claude

    var id: String { rawValue }
}

struct Texts {
    let language: AppLanguage

    func text(_ zh: String, _ en: String) -> String {
        language == .zh ? zh : en
    }
}

// Built-in presets for common domestic OpenAI-compatible providers. They all
// speak Chat Completions (not Responses), so each enables the Chat adapter.
// base_url and a sensible default model are filled in; the user only pastes
// their API key (and can tweak the model id).
struct ProviderPreset: Identifiable {
    let id: String
    let name: String
    let baseURL: String
    let model: String
    let displayName: String

    static let all: [ProviderPreset] = [
        ProviderPreset(id: "deepseek", name: "DeepSeek 深度求索", baseURL: "https://api.deepseek.com/v1", model: "deepseek-chat", displayName: "DeepSeek"),
        ProviderPreset(id: "kimi", name: "Kimi 月之暗面", baseURL: "https://api.moonshot.cn/v1", model: "kimi-k2-0905-preview", displayName: "Kimi"),
        ProviderPreset(id: "zhipu", name: "智谱 GLM", baseURL: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6", displayName: "智谱 GLM"),
        ProviderPreset(id: "qwen", name: "通义千问 Qwen", baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen3-coder-plus", displayName: "通义千问"),
        ProviderPreset(id: "doubao", name: "豆包 Doubao（火山引擎）", baseURL: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-seed-1-6-251015", displayName: "豆包"),
        ProviderPreset(id: "baidu", name: "百度文心 ERNIE（千帆）", baseURL: "https://qianfan.baidubce.com/v2", model: "ernie-4.5-turbo-128k", displayName: "文心一言"),
        ProviderPreset(id: "minimax", name: "MiniMax", baseURL: "https://api.minimaxi.com/v1", model: "MiniMax-M2", displayName: "MiniMax"),
        ProviderPreset(id: "stepfun", name: "阶跃星辰 StepFun", baseURL: "https://api.stepfun.com/v1", model: "step-2-16k", displayName: "StepFun"),
    ]
}

struct CommandResult {
    let status: Int32
    let output: String
}

enum UpdateState: Equatable {
    case idle
    case checking
    case upToDate
    case available(version: String)
    case failed
}

private enum VersionComparator {
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        let candidateParts = numericParts(candidate)
        let currentParts = numericParts(current)
        let count = max(candidateParts.count, currentParts.count)

        for index in 0..<count {
            let candidateValue = index < candidateParts.count ? candidateParts[index] : 0
            let currentValue = index < currentParts.count ? currentParts[index] : 0
            if candidateValue != currentValue {
                return candidateValue > currentValue
            }
        }
        return false
    }

    private static func numericParts(_ version: String) -> [Int] {
        version
            .trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
            .split(separator: ".")
            .map { component in
                Int(component.prefix(while: { $0.isNumber })) ?? 0
            }
    }
}

final class SwitchViewModel: ObservableObject {
    @Published var statusValues: [String: String] = [:]
    @Published var localBaseURL = ""
    @Published var localModel = ""
    @Published var localModelDisplayName = ""
    @Published var useChatAdapter = true
    @Published var skipLogin = false
    @Published var replacementAPIKey = ""
    @Published var officialModel = ""
    @Published var output = ""
    @Published var isBusy = false
    @Published var switchSucceeded = false
    @Published var switchFailed = false
    @Published var completedMode = ProviderMode.custom
    @Published var completedTarget = SwitchTarget.codex
    @Published var updateState = UpdateState.idle
    @Published var releaseURL = URL(string: "https://github.com/kuangre123/codex-switch/releases/latest")!

    var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
    }

    private var cliPath: String {
        if let bundled = Bundle.main.path(forResource: "codex-switch", ofType: nil) {
            return bundled
        }
        return NSString(string: "~/.local/bin/codex-switch").expandingTildeInPath
    }

    func load(target: SwitchTarget = .codex) {
        checkForUpdates()
        isBusy = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let status = self.run(self.statusArguments(for: target))
            let config = self.run(self.configShowArguments(for: target))
            DispatchQueue.main.async {
                self.statusValues = self.parse(status.output)
                let values = self.parse(config.output)
                self.localBaseURL = values["local_base_url"] ?? (target == .claude ? "http://127.0.0.1:15721" : "https://jp.icodeeasy.cc")
                self.localModel = values["local_upstream_model"] ?? values["local_model"] ?? (target == .claude ? "claude-sonnet-4-6" : "gpt-5.5")
                self.localModelDisplayName = values["local_model_display_name"] ?? self.localModel
                self.useChatAdapter = (values["chat_adapter"] ?? "true") != "false"
                self.skipLogin = (values["skip_login"] ?? "false") == "true"
                self.officialModel = values["official_model"] ?? (target == .claude ? "claude-sonnet-4-6" : "gpt-5.5")
                if status.status != 0 || config.status != 0 {
                    self.output = [status.output, config.output].filter { !$0.isEmpty }.joined(separator: "\n")
                }
                self.isBusy = false
            }
        }
    }

    func checkForUpdates() {
        guard updateState != .checking else { return }
        updateState = .checking

        let endpoint = URL(string: "https://github.com/kuangre123/codex-switch/releases/latest")!
        var request = URLRequest(url: endpoint)
        request.httpMethod = "HEAD"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Codex-Switch/\(currentVersion)", forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 15

        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            guard let self else { return }
            guard error == nil,
                  let httpResponse = response as? HTTPURLResponse,
                  (200..<300).contains(httpResponse.statusCode),
                  let releaseURL = httpResponse.url,
                  releaseURL.path.contains("/releases/tag/") else {
                DispatchQueue.main.async {
                    self.updateState = .failed
                }
                return
            }

            let version = releaseURL.lastPathComponent.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
            DispatchQueue.main.async {
                self.releaseURL = releaseURL
                self.updateState = VersionComparator.isNewer(version, than: self.currentVersion)
                    ? .available(version: version)
                    : .upToDate
            }
        }.resume()
    }

    func performUpdateAction() {
        switch updateState {
        case .available:
            NSWorkspace.shared.open(releaseURL)
        case .checking:
            break
        case .idle, .upToDate, .failed:
            checkForUpdates()
        }
    }

    func switchProvider(to mode: ProviderMode, target: SwitchTarget) {
        isBusy = true
        output = ""
        switchSucceeded = false
        switchFailed = false
        let baseURL = localBaseURL
        let local = localModel
        let displayName = localModelDisplayName
        let useAdapter = useChatAdapter
        let doSkipLogin = skipLogin
        let replacementKey = replacementAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let official = officialModel
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            if target == .codex {
                self.configureCodex(
                    baseURL: baseURL,
                    customModel: local,
                    displayName: displayName,
                    officialModel: official,
                    mode: mode,
                    useChatAdapter: useAdapter,
                    skipLogin: doSkipLogin,
                    replacementKey: replacementKey
                )
                return
            }
            let save = self.run(self.configSetArguments(for: target) + [
                "--local-base-url", baseURL,
                "--local-model", local,
                "--official-model", official,
            ])
            guard save.status == 0 else {
                DispatchQueue.main.async {
                    self.output = save.output
                    self.isBusy = false
                    self.switchFailed = true
                }
                return
            }
            var switchArguments = self.switchArguments(for: target, mode: mode)
            var switchInput: String?
            if mode == .custom && !replacementKey.isEmpty {
                switchArguments.append(target == .claude ? "--auth-token-stdin" : "--api-key-stdin")
                switchInput = replacementKey + "\n"
            }
            let switched = self.run(switchArguments, standardInput: switchInput)
            let status = self.run(self.statusArguments(for: target))
            DispatchQueue.main.async {
                self.output = switched.output
                self.statusValues = self.parse(status.output)
                self.isBusy = false
                self.completedMode = mode
                self.completedTarget = target
                if switched.status == 0 {
                    self.replacementAPIKey = ""
                    self.switchSucceeded = true
                } else {
                    self.switchFailed = true
                }
            }
        }
    }

    private func configureCodex(baseURL: String, customModel: String, displayName: String, officialModel: String, mode: ProviderMode, useChatAdapter: Bool, skipLogin: Bool, replacementKey: String) {
        let defaultProvider = mode == .custom ? "custom" : "openai"
        var arguments = [
            "configure",
            "--base-url", baseURL,
            "--custom-model", customModel,
            "--custom-model-name", displayName,
            "--official-model", officialModel,
            "--default-provider", defaultProvider,
            "--restart-codex",
        ]
        if useChatAdapter {
            arguments.append("--chat-adapter")
        }
        if skipLogin {
            arguments.append("--skip-login")
        }
        var switchInput: String?
        if !replacementKey.isEmpty {
            arguments.append("--api-key-stdin")
            switchInput = replacementKey + "\n"
        }
        let configured = run(arguments, standardInput: switchInput)
        let status = run(["status"])
        DispatchQueue.main.async {
            self.output = configured.output
            self.statusValues = self.parse(status.output)
            self.isBusy = false
            self.completedTarget = .codex
            self.completedMode = mode
            if configured.status == 0 {
                self.replacementAPIKey = ""
                self.switchSucceeded = true
            } else {
                self.switchFailed = true
            }
        }
    }

    private func statusArguments(for target: SwitchTarget) -> [String] {
        target == .claude ? ["claude-status"] : ["status"]
    }

    private func configShowArguments(for target: SwitchTarget) -> [String] {
        target == .claude ? ["claude-config", "show"] : ["config", "show"]
    }

    private func configSetArguments(for target: SwitchTarget) -> [String] {
        target == .claude ? ["claude-config", "set"] : ["config", "set"]
    }

    private func switchArguments(for target: SwitchTarget, mode: ProviderMode) -> [String] {
        if target == .claude {
            return [mode == .custom ? "claude-local" : "claude-official"]
        }
        return [mode == .custom ? "local" : "official", "--migrate-latest", "--restart-codex"]
    }

    private func run(_ arguments: [String], standardInput: String? = nil) -> CommandResult {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            return CommandResult(status: 127, output: "codex-switch CLI not found at \(cliPath)")
        }
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: cliPath)
        process.arguments = arguments
        process.standardOutput = pipe
        process.standardError = pipe
        let inputPipe = standardInput == nil ? nil : Pipe()
        process.standardInput = inputPipe
        do {
            try process.run()
            if let standardInput, let inputPipe {
                inputPipe.fileHandleForWriting.write(Data(standardInput.utf8))
                inputPipe.fileHandleForWriting.closeFile()
            }
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return CommandResult(status: process.terminationStatus, output: text)
        } catch {
            return CommandResult(status: 126, output: error.localizedDescription)
        }
    }

    private func parse(_ output: String) -> [String: String] {
        var values: [String: String] = [:]
        for line in output.split(separator: "\n") {
            let parts = line.split(separator: ":", maxSplits: 1).map(String.init)
            if parts.count == 2 {
                values[parts[0]] = parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        return values
    }
}

struct ContentView: View {
    @StateObject private var model = SwitchViewModel()
    @AppStorage("language") private var languageRaw = AppLanguage.zh.rawValue
    @State private var targetTool = SwitchTarget.codex
    @State private var targetMode = ProviderMode.custom

    private var language: AppLanguage {
        AppLanguage(rawValue: languageRaw) ?? .zh
    }

    private var texts: Texts { Texts(language: language) }
    private var officialModelOptions: [String] {
        targetTool == .claude
            ? ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"]
            : ["gpt-5.1-codex-max", "gpt-5.1-codex", "gpt-5.1", "gpt-5.5"]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            statusCard
            settingsCard
            if !model.output.isEmpty {
                outputCard
            }
            Spacer(minLength: 0)
            footer
        }
        .padding(24)
        .frame(minWidth: 660, minHeight: targetMode == .custom ? 520 : 460)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    model.performUpdateAction()
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: updateButtonIcon)
                        Text(updateButtonTitle)
                    }
                }
                .disabled(model.updateState == .checking)
                .help(updateButtonHelp)
            }
        }
        .overlay {
            if model.isBusy {
                ZStack {
                    Color.black.opacity(0.08)
                    ProgressView(progressText)
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            } else if model.switchSucceeded || model.switchFailed {
                resultOverlay
            }
        }
        .onAppear {
            model.load(target: targetTool)
        }
        .onChange(of: targetTool) { newValue in
            model.replacementAPIKey = ""
            model.load(target: newValue)
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Codex Switch")
                    .font(.largeTitle.bold())
                Text(texts.text("并行配置 Codex 的官方和自定义 API。", "Configure Codex official and custom APIs side by side."))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Picker(texts.text("语言", "Language"), selection: $languageRaw) {
                Text("中文").tag(AppLanguage.zh.rawValue)
                Text("English").tag(AppLanguage.en.rawValue)
            }
            .pickerStyle(.menu)
            .frame(width: 130)
        }
    }

    private var appVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
    }

    private var footer: some View {
        HStack(spacing: 6) {
            Spacer()
            Text(verbatim: "v\(appVersion)")
            Text(verbatim: "·")
            Text(texts.text("作者：狂热AI（X：CrazyAIAgent）", "By 狂热AI (X: CrazyAIAgent)"))
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    private var statusCard: some View {
        GroupBox(texts.text("当前状态", "Current Status")) {
            Grid(alignment: .leading, horizontalSpacing: 22, verticalSpacing: 10) {
                statusRow(texts.text("Provider", "Provider"), model.statusValues["model_provider"] ?? "-")
                statusRow(texts.text("模型", "Model"), model.statusValues["model"] ?? "-")
                statusRow(texts.text("自定义地址", "Custom URL"), model.statusValues["custom.base_url"] ?? "-")
                if targetTool == .codex {
                    statusRow(texts.text("模型目录", "Model Catalog"), model.statusValues["model_catalog_json"] ?? "-")
                }
                statusRow(texts.text("API Key", "API Key"), model.statusValues["api_key"] ?? "-")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 6)
        }
    }

    private var settingsCard: some View {
        GroupBox(texts.text("配置设置", "Setup")) {
            VStack(alignment: .leading, spacing: 14) {
                settingRow(texts.text("选择 API 提供方", "API Provider")) {
                    HStack(spacing: 12) {
                        Button {
                            targetMode = .custom
                        } label: {
                            Text(texts.text("自定义 API", "Custom API"))
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(targetMode == .custom ? .blue : .gray)
                        .controlSize(.regular)

                        Button {
                            targetMode = .official
                        } label: {
                            Text(officialProviderTitle)
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(targetMode == .official ? .blue : .gray)
                        .controlSize(.regular)
                    }
                }

                if targetTool == .codex {
                    Text(texts.text("官方和自定义 provider 会同时保留；这里选择默认使用哪一路（桌面端和 CLI 通用）。", "Official and custom providers are both kept; choose the default route (applies to both the desktop app and the CLI)."))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                VStack(alignment: .leading, spacing: 12) {
                    if targetTool == .codex || targetMode == .custom {
                        if targetTool == .codex && targetMode == .custom {
                            settingRow(texts.text("快速预设", "Quick Preset")) {
                                Menu {
                                    ForEach(ProviderPreset.all) { preset in
                                        Button(preset.name) {
                                            model.localBaseURL = preset.baseURL
                                            model.localModel = preset.model
                                            model.localModelDisplayName = preset.displayName
                                            model.useChatAdapter = true
                                        }
                                    }
                                } label: {
                                    Label(texts.text("选择常用国内模型…", "Pick a preset provider…"), systemImage: "wand.and.stars")
                                }
                                .help(texts.text("一键填入接入点与模型，之后只需粘贴 API Key", "Fills the endpoint and model; just paste your API key"))
                            }
                        }
                        settingRow(texts.text("自定义 API 地址", "Custom API URL")) {
                            TextField("https://example.com", text: $model.localBaseURL)
                        }
                        settingRow(texts.text(targetTool == .codex ? "上游模型 ID" : "自定义模型 ID", targetTool == .codex ? "Upstream Model ID" : "Custom Model ID")) {
                            TextField(targetTool == .claude ? "claude-sonnet-4-6" : "gpt-5.5", text: $model.localModel)
                        }
                        if targetTool == .codex {
                            settingRow(texts.text("显示名称", "Display Name")) {
                                TextField(texts.text("我的模型", "My Model"), text: $model.localModelDisplayName)
                            }
                            settingRow(texts.text("Chat 适配器", "Chat Adapter")) {
                                Toggle(texts.text("把 Chat Completions 转成 Responses", "Bridge Chat Completions to Responses"), isOn: $model.useChatAdapter)
                                    .toggleStyle(.checkbox)
                            }
                            settingRow(texts.text("跳过登录", "Skip Login")) {
                                Toggle(texts.text("绕过 ChatGPT OAuth 登录验证", "Bypass ChatGPT OAuth login"), isOn: $model.skipLogin)
                                    .toggleStyle(.checkbox)
                            }
                        }
                        settingRow(texts.text("新 API Key", "New API Key")) {
                            SecureField(apiKeyPlaceholder, text: $model.replacementAPIKey)
                                .help(texts.text("留空继续使用现有 API Key", "Leave blank to keep the saved API key"))
                        }
                    }
                    if targetMode == .official {
                        settingRow(texts.text("官方模型", "Official Model")) {
                            HStack(spacing: 8) {
                                TextField(targetTool == .claude ? "claude-sonnet-4-6" : "gpt-5.5", text: $model.officialModel)
                                Menu {
                                    ForEach(officialModelOptions, id: \.self) { option in
                                        Button(option) {
                                            model.officialModel = option
                                        }
                                    }
                                } label: {
                                    Image(systemName: "chevron.down.circle")
                                        .imageScale(.large)
                                }
                                .menuStyle(.borderlessButton)
                                .help(texts.text("选择官方模型", "Choose official model"))
                            }
                        }
                    }
                }

                Button {
                    model.switchProvider(to: targetMode, target: targetTool)
                } label: {
                    Label(primaryButtonTitle, systemImage: targetTool == .codex ? "square.and.arrow.down" : "arrow.triangle.2.circlepath")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(model.isBusy)
            }
            .padding(.top, 6)
        }
    }

    private var progressText: String {
        targetTool == .codex
            ? texts.text("正在保存并重启 Codex…", "Saving and restarting Codex…")
            : texts.text("正在切换 Claude Code 配置…", "Switching Claude Code settings…")
    }

    private var primaryButtonTitle: String {
        targetTool == .codex
            ? texts.text("保存配置并重启 Codex", "Save Setup and Restart Codex")
            : texts.text("保存配置", "Save Settings")
    }

    private var officialProviderTitle: String {
        targetTool == .claude
            ? texts.text("官方 Claude", "Official Claude")
            : texts.text("官方 OpenAI", "Official OpenAI")
    }

    private var outputCard: some View {
        GroupBox(texts.text("执行结果", "Result")) {
            ScrollView {
                Text(model.output)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 6)
            }
            .frame(maxHeight: 120)
        }
    }

    private var resultOverlay: some View {
        ZStack {
            Color.black.opacity(0.22)
                .ignoresSafeArea()

            VStack(spacing: 18) {
                Image(systemName: model.switchSucceeded ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .font(.system(size: 42))
                    .foregroundStyle(model.switchSucceeded ? Color.green : Color.red)

                Text(model.switchSucceeded
                    ? texts.text("操作成功", "Success")
                    : texts.text("操作失败", "Failed"))
                    .font(.title2.bold())

                Text(resultMessage)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: 380)

                Button(texts.text("确定", "OK")) {
                    model.switchSucceeded = false
                    model.switchFailed = false
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .frame(maxWidth: .infinity)
            }
            .padding(26)
            .frame(width: 440)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            .shadow(radius: 24)
        }
    }

    private var resultMessage: String {
        if model.switchFailed {
            return model.output.isEmpty
                ? texts.text("请检查配置后重试。", "Check the configuration and try again.")
                : model.output
        }
        if model.completedTarget == .claude {
            return model.completedMode == .custom
                ? texts.text("已切换 Claude Code 到自定义 API。请重新打开 Claude Code 终端会话让设置生效。", "Switched Claude Code to the custom API. Restart Claude Code terminal sessions to apply it.")
                : texts.text("已切换 Claude Code 到官方 Claude。请重新打开 Claude Code 终端会话让设置生效。", "Switched Claude Code to Official Claude. Restart Claude Code terminal sessions to apply it.")
        }
        return texts.text("已保存官方 OpenAI 和自定义 API 的并行配置，并已重启 Codex。请在 Codex 的模型选择器里选择需要的模型。", "Saved the parallel Official OpenAI and custom API setup, then restarted Codex. Choose the model in Codex's model picker.")
    }

    private var apiKeyPlaceholder: String {
        let savedKey = model.statusValues["api_key"] ?? ""
        if savedKey.isEmpty || savedKey == "(none)" || savedKey == "-" {
            return texts.text("输入 API Key", "Enter API key")
        }
        return texts.text("留空沿用 \(savedKey)", "Leave blank to keep \(savedKey)")
    }

    private func settingRow<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 14) {
            Text(label)
                .frame(width: 130, alignment: .leading)
            content()
        }
    }

    private var updateButtonTitle: String {
        switch model.updateState {
        case .idle:
            return texts.text("检查更新", "Check Updates")
        case .checking:
            return texts.text("检查中…", "Checking…")
        case .upToDate:
            return texts.text("已是最新版", "Up to Date")
        case let .available(version):
            return texts.text("发现 v\(version)", "Update v\(version)")
        case .failed:
            return texts.text("重试检查", "Retry Check")
        }
    }

    private var updateButtonIcon: String {
        switch model.updateState {
        case .idle:
            return "arrow.clockwise.circle"
        case .checking:
            return "arrow.triangle.2.circlepath"
        case .upToDate:
            return "checkmark.circle"
        case .available:
            return "arrow.down.circle.fill"
        case .failed:
            return "exclamationmark.triangle"
        }
    }

    private var updateButtonHelp: String {
        switch model.updateState {
        case .available:
            return texts.text("打开 GitHub 下载新版本", "Open GitHub to download the update")
        case .upToDate:
            return texts.text("当前版本 v\(model.currentVersion)，点击重新检查", "Current version v\(model.currentVersion). Click to check again.")
        case .failed:
            return texts.text("检查失败，点击重试", "Update check failed. Click to retry.")
        default:
            return texts.text("当前版本 v\(model.currentVersion)", "Current version v\(model.currentVersion)")
        }
    }

    private func statusRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 110, alignment: .leading)
            Text(value)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
        }
    }
}

@main
struct CodexSwitchApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
