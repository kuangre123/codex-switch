import AppKit
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

struct Texts {
    let language: AppLanguage

    func text(_ zh: String, _ en: String) -> String {
        language == .zh ? zh : en
    }
}

struct CommandResult {
    let status: Int32
    let output: String
}

final class SwitchViewModel: ObservableObject {
    @Published var statusValues: [String: String] = [:]
    @Published var localBaseURL = ""
    @Published var localModel = ""
    @Published var officialModel = ""
    @Published var output = ""
    @Published var isBusy = false
    @Published var switchSucceeded = false
    @Published var switchFailed = false
    @Published var completedMode = ProviderMode.custom

    private var cliPath: String {
        NSString(string: "~/.local/bin/codex-switch").expandingTildeInPath
    }

    func load() {
        isBusy = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let status = self.run(["status"])
            let config = self.run(["config", "show"])
            DispatchQueue.main.async {
                self.statusValues = self.parse(status.output)
                let values = self.parse(config.output)
                self.localBaseURL = values["local_base_url"] ?? "https://jp.icodeeasy.cc"
                self.localModel = values["local_model"] ?? "gpt-5.5"
                self.officialModel = values["official_model"] ?? "gpt-5.5"
                if status.status != 0 || config.status != 0 {
                    self.output = [status.output, config.output].filter { !$0.isEmpty }.joined(separator: "\n")
                }
                self.isBusy = false
            }
        }
    }

    func switchProvider(to mode: ProviderMode) {
        isBusy = true
        output = ""
        switchSucceeded = false
        switchFailed = false
        let baseURL = localBaseURL
        let local = localModel
        let official = officialModel
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let save = self.run([
                "config", "set",
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
            let command = mode == .custom ? "local" : "official"
            let switched = self.run([command, "--migrate-latest", "--restart-codex"])
            let status = self.run(["status"])
            DispatchQueue.main.async {
                self.output = switched.output
                self.statusValues = self.parse(status.output)
                self.isBusy = false
                self.completedMode = mode
                if switched.status == 0 {
                    self.switchSucceeded = true
                } else {
                    self.switchFailed = true
                }
            }
        }
    }

    private func run(_ arguments: [String]) -> CommandResult {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            return CommandResult(status: 127, output: "codex-switch CLI not found at \(cliPath)")
        }
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: cliPath)
        process.arguments = arguments
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
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
    @State private var targetMode = ProviderMode.custom

    private var language: AppLanguage {
        AppLanguage(rawValue: languageRaw) ?? .zh
    }

    private var texts: Texts { Texts(language: language) }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            statusCard
            settingsCard
            if !model.output.isEmpty {
                outputCard
            }
            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(minWidth: 660, minHeight: 520)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    NSWorkspace.shared.open(URL(string: "https://github.com/kuangre123/codex-switch/releases/latest")!)
                } label: {
                    Label(texts.text("升级", "Upgrade"), systemImage: "arrow.up.circle")
                }
            }
        }
        .overlay {
            if model.isBusy {
                ZStack {
                    Color.black.opacity(0.08)
                    ProgressView(texts.text("正在切换并重启 Codex…", "Switching and restarting Codex…"))
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            } else if model.switchSucceeded || model.switchFailed {
                resultOverlay
            }
        }
        .onAppear {
            model.load()
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Codex Switch")
                    .font(.largeTitle.bold())
                Text(texts.text("切换 API，同步会话上下文，并重启 Codex 让当前运行时生效。", "Switch APIs, sync thread context, and restart Codex so the running app picks it up."))
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

    private var statusCard: some View {
        GroupBox(texts.text("当前状态", "Current Status")) {
            Grid(alignment: .leading, horizontalSpacing: 22, verticalSpacing: 10) {
                statusRow(texts.text("Provider", "Provider"), model.statusValues["model_provider"] ?? "-")
                statusRow(texts.text("模型", "Model"), model.statusValues["model"] ?? "-")
                statusRow(texts.text("自定义地址", "Custom URL"), model.statusValues["custom.base_url"] ?? "-")
                statusRow(texts.text("API Key", "API Key"), model.statusValues["api_key"] ?? "-")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 6)
        }
    }

    private var settingsCard: some View {
        GroupBox(texts.text("切换设置", "Switch Settings")) {
            VStack(alignment: .leading, spacing: 14) {
                Picker(texts.text("目标模式", "Target Mode"), selection: $targetMode) {
                    Text(texts.text("自定义 API", "Custom API")).tag(ProviderMode.custom)
                    Text(texts.text("官方 OpenAI", "Official OpenAI")).tag(ProviderMode.official)
                }
                .pickerStyle(.segmented)

                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 12) {
                    GridRow {
                        Text(texts.text("自定义 API 地址", "Custom API URL"))
                            .frame(width: 130, alignment: .leading)
                        TextField("https://example.com", text: $model.localBaseURL)
                    }
                    GridRow {
                        Text(texts.text("自定义模型", "Custom Model"))
                            .frame(width: 130, alignment: .leading)
                        TextField("gpt-5.5", text: $model.localModel)
                    }
                    GridRow {
                        Text(texts.text("官方模型", "Official Model"))
                            .frame(width: 130, alignment: .leading)
                        TextField("gpt-5.5", text: $model.officialModel)
                    }
                }

                Button {
                    model.switchProvider(to: targetMode)
                } label: {
                    Label(texts.text("确认切换", "Confirm Switch"), systemImage: "arrow.triangle.2.circlepath")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(model.isBusy)
            }
            .padding(.top, 6)
        }
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
                    ? texts.text("切换成功", "Switch Successful")
                    : texts.text("切换失败", "Switch Failed"))
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
        return model.completedMode == .custom
            ? texts.text("已切换到自定义 API，并已重启 Codex 让设置生效。", "Switched to the custom API and restarted Codex to apply it.")
            : texts.text("已切换到官方 OpenAI，并已重启 Codex 让设置生效。", "Switched to Official OpenAI and restarted Codex to apply it.")
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
