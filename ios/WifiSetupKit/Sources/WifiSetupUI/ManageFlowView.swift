#if os(iOS)
import SwiftUI
import UIKit
import WifiSetupKit

/// Owns the ManageClient for a sticker link; another sticker starts over.
public struct ManageScreen: View {
    let link: ManageLink
    var rescanQR: (() -> Void)?
    var close: (() -> Void)?
    @State private var client: ManageClient?

    public init(link: ManageLink, rescanQR: (() -> Void)? = nil, close: (() -> Void)? = nil) {
        self.link = link
        self.rescanQR = rescanQR
        self.close = close
    }

    public var body: some View {
        Group {
            if let client {
                ManageFlowView(client: client, rescanQR: rescanQR, close: close)
            } else {
                Theme.background.ignoresSafeArea()
            }
        }
        .task(id: link) {
            client?.stop()
            let transport: ManageTransport = SetupLaunch.isMock || SetupLaunch.isMockManage || link.isDemo
                ? MockManageTransport.demo(link: link) : ManageBLETransport()
            let new = ManageClient(link: link, transport: transport)
            new.start()
            client = new
        }
        .onDisappear { client?.stop() }
    }
}

/// Status and wifi of a paired screen, opened from the QR sticker on the player.
struct ManageFlowView: View {
    let client: ManageClient
    var rescanQR: (() -> Void)?
    var close: (() -> Void)?

    private var title: String {
        client.status?.name ?? "Scherm \(client.link.deviceID)"
    }

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.background.ignoresSafeArea())
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(Theme.background, for: .navigationBar)
                .toolbar {
                    if client.phase == .choosing {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Status", systemImage: "chevron.left") { client.backToStatus() }
                                .labelStyle(.titleAndIcon)
                                .accessibilityIdentifier("button.backToStatus")
                        }
                    } else if let close {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Klaar", action: close)
                        }
                    }
                }
                .animation(.smooth(duration: 0.25), value: client.phase)
        }
        .tint(Theme.accent)
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var content: some View {
        switch client.phase {
        case let .searching(slow):
            StepView(systemImage: "antenna.radiowaves.left.and.right", busy: !slow,
                     title: slow ? "Scherm nog niet gevonden" : "Scherm zoeken…",
                     message: slow
                        ? "Ga dichter bij de speler staan en controleer of hij aan staat. We blijven zoeken."
                        : "Houd je telefoon in de buurt van de speler.")
                .accessibilityIdentifier("step.searching")
        case .connecting:
            StepView(systemImage: "dot.radiowaves.left.and.right", busy: true,
                     title: "Verbinden met het scherm…", message: nil)
                .accessibilityIdentifier("step.connecting")
        case let .bluetooth(problem):
            ManageBluetoothView(problem: problem)
        case .status:
            ManageStatusView(client: client)
        case .choosing:
            ManageNetworkListView(client: client)
        case let .working(message):
            StepView(systemImage: "wifi", busy: true, title: message,
                     message: "Het scherm wisselt van netwerk. Dit duurt meestal een halve minuut. Laat deze app open.")
                .accessibilityIdentifier("step.working")
        case let .joined(ssid, online, message):
            StepView(systemImage: "checkmark.circle.fill", title: "Verbonden met \(ssid)",
                     message: online ? "Het scherm is online." : message
                        ?? "Het scherm is verbonden, maar de server is nog niet bereikbaar.") {
                Button("Klaar") { client.backToStatus() }
                    .buttonStyle(PrimaryButtonStyle())
                    .padding(.top, 12)
                    .accessibilityIdentifier("button.done")
            }
            .accessibilityIdentifier("step.joined")
        case let .joinFailed(message, ssid):
            StepView(systemImage: "wifi.exclamationmark", tint: Theme.error, title: "Dat lukte niet",
                     message: message) {
                VStack(spacing: 12) {
                    Button("Opnieuw proberen") { client.backToList() }
                        .buttonStyle(PrimaryButtonStyle())
                        .accessibilityIdentifier("button.retry")
                    Button(ssid.map { "Naar status (\($0))" } ?? "Naar status") { client.backToStatus() }
                        .accessibilityIdentifier("button.status")
                }
                .padding(.top, 12)
            }
            .accessibilityIdentifier("step.failed")
        case .expired:
            StepView(systemImage: "qrcode.viewfinder", tint: Theme.error, title: "QR verlopen",
                     message: ManageClient.expiredMessage) {
                if let rescanQR {
                    Button("Andere QR-code scannen", action: rescanQR)
                        .buttonStyle(PrimaryButtonStyle())
                        .padding(.top, 12)
                }
            }
            .accessibilityIdentifier("step.expired")
        case .outdated:
            StepView(systemImage: "arrow.down.app", tint: Theme.error, title: "Werk de app bij",
                     message: "Dit scherm gebruikt een nieuwere versie van het beheer. Werk de app bij in de App Store en scan de sticker opnieuw.")
                .accessibilityIdentifier("step.outdated")
        case .unsupported:
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.error, title: "Beheer nog niet beschikbaar",
                     message: "De speler van dit scherm is nog niet bijgewerkt voor beheer via de sticker. Probeer het later opnieuw.")
                .accessibilityIdentifier("step.unsupported")
        case let .failed(message):
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.error, title: "Dat lukte niet",
                     message: message) {
                Button("Opnieuw proberen") { client.retry() }
                    .buttonStyle(PrimaryButtonStyle())
                    .padding(.top, 12)
                    .accessibilityIdentifier("button.restart")
            }
            .accessibilityIdentifier("step.error")
        }
    }
}

private struct ManageStatusView: View {
    let client: ManageClient

    var body: some View {
        List {
            if client.isReconnecting {
                Section {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Verbinding met het scherm herstellen…").foregroundStyle(Theme.muted)
                    }
                    .listRowBackground(Theme.card)
                    .accessibilityIdentifier("label.reconnecting")
                }
            }
            if let status = client.status {
                Section {
                    OnlineHeader(status: status, localName: client.link.localName)
                        .listRowBackground(Theme.card)
                }
                Section("Netwerk") {
                    row("Wifi", value: wifiText(status), id: "label.network") {
                        if let signal = status.signal, status.ssid != nil {
                            Image(systemName: "wifi", variableValue: Double(signal) / 100)
                                .foregroundStyle(Theme.soft)
                        }
                    }
                    if status.network == "ethernet" {
                        row("Kabel", value: "Verbonden", id: "label.ethernet")
                    }
                    row("IP-adres", value: status.ip ?? "Geen", id: "label.ip")
                }
                Section("Scherm") {
                    row("Toont", value: screenText(status.screenState), id: "label.screen")
                    row("Gekoppeld", value: status.paired.map { $0 ? "Ja" : "Nee" } ?? "Onbekend", id: "label.paired")
                    row("Spelerversie", value: status.agentVersion ?? "Onbekend", id: "label.version")
                    if let device = status.device {
                        row("Apparaat", value: device, id: "label.device")
                    }
                }
                if let job = status.job, job.op == "connect", let msg = job.msg {
                    Section("Laatste wifi-wijziging") {
                        Text(msg)
                            .font(.subheadline)
                            .foregroundStyle(job.state == "failed" ? Theme.error : Theme.muted)
                            .listRowBackground(Theme.card)
                            .accessibilityIdentifier("label.job")
                    }
                }
                if status.canChangeWifi {
                    Section {
                        Button("Wifi wijzigen") { client.chooseNetwork() }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(!client.isConnected)
                            .listRowInsets(EdgeInsets())
                            .listRowBackground(Color.clear)
                            .accessibilityIdentifier("button.changeWifi")
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .refreshable { await client.refreshStatus() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if client.isRefreshing {
                    ProgressView()
                } else {
                    Button("Vernieuwen", systemImage: "arrow.clockwise") {
                        Task { await client.refreshStatus() }
                    }
                    .disabled(!client.isConnected)
                    .accessibilityIdentifier("button.refresh")
                }
            }
        }
        .accessibilityIdentifier("step.status")
    }

    private func row(_ label: String, value: String, id: String) -> some View {
        row(label, value: value, id: id) { EmptyView() }
    }

    private func row<Accessory: View>(_ label: String, value: String, id: String,
                                      @ViewBuilder accessory: () -> Accessory) -> some View {
        HStack(spacing: 8) {
            Text(label).foregroundStyle(Theme.muted)
            Spacer()
            Text(value)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            accessory()
        }
        .listRowBackground(Theme.card)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(id)
    }

    private func wifiText(_ status: ManageStatus) -> String {
        guard let ssid = status.ssid else { return "Geen" }
        return status.signal.map { "\(ssid) · \($0)%" } ?? ssid
    }

    private func screenText(_ state: String?) -> String {
        switch state {
        case "PLAYING": "Content"
        case "WAITING": "Wacht op content"
        case "PAIRING": "Koppelcode"
        case "WIFI_SETUP": "Wifi instellen"
        case let other?: other
        case nil: "Onbekend"
        }
    }
}

private struct OnlineHeader: View {
    let status: ManageStatus
    let localName: String

    private var online: Bool? { status.online }
    private var tint: Color { online == true ? Theme.accent : Theme.error }

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().fill(tint.opacity(0.14)).frame(width: 52, height: 52)
                Image(systemName: online == true ? "checkmark.circle.fill" : "wifi.exclamationmark")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(tint)
            }
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(status.name ?? localName)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .accessibilityIdentifier("label.screenName")
                Text(online == true ? "Online" : online == false ? "Offline: de server is niet bereikbaar" : "Online-status onbekend")
                    .font(.subheadline)
                    .foregroundStyle(online == true ? Theme.accent : Theme.muted)
                    .accessibilityIdentifier("label.online")
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }
}

private struct ManageNetworkListView: View {
    let client: ManageClient
    @State private var choice: NetworkListView.Choice?

    var body: some View {
        List {
            Section {
                if let notice = client.notice {
                    Label(notice, systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline)
                        .foregroundStyle(Theme.error)
                        .listRowBackground(Theme.error.opacity(0.12))
                        .accessibilityIdentifier("label.notice")
                }
                ForEach(client.networks) { network in
                    Button { choice = .init(network: network) } label: {
                        HStack {
                            NetworkRow(network: network)
                            if network.ssid == client.status?.ssid {
                                Image(systemName: "checkmark").foregroundStyle(Theme.accent)
                            }
                        }
                    }
                    .accessibilityIdentifier("network.\(network.ssid)")
                }
                .listRowBackground(Theme.card)
                if client.networks.isEmpty {
                    Text(client.isScanning ? "Netwerken zoeken…" : "Geen netwerken gevonden. Trek omlaag om opnieuw te zoeken.")
                        .foregroundStyle(Theme.muted)
                        .listRowBackground(Theme.card)
                }
                Button { choice = .init(network: nil) } label: {
                    Label("Ander netwerk…", systemImage: "plus")
                        .foregroundStyle(Theme.accent)
                }
                .listRowBackground(Theme.card)
                .accessibilityIdentifier("network.other")
            } header: {
                Text("Kies het nieuwe wifi-netwerk voor het scherm")
                    .textCase(nil)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
            } footer: {
                Text("Lukt het niet, dan gaat het scherm terug naar het netwerk dat het nu heeft.")
                    .foregroundStyle(Theme.muted)
            }
        }
        .scrollContentBackground(.hidden)
        .refreshable {
            client.rescan()
            while client.isScanning {
                try? await Task.sleep(for: .milliseconds(300))
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if client.isScanning {
                    ProgressView()
                } else {
                    Button("Vernieuwen", systemImage: "arrow.clockwise") { client.rescan() }
                        .accessibilityIdentifier("button.rescan")
                }
            }
        }
        .sheet(item: $choice) { choice in
            PasswordSheet(network: choice.network) { ssid, password in
                self.choice = nil
                client.connect(ssid: ssid, password: password, hidden: choice.network == nil)
            }
        }
        .accessibilityIdentifier("step.choosing")
    }
}

private struct ManageBluetoothView: View {
    let problem: BluetoothProblem
    @Environment(\.openURL) private var openURL

    var body: some View {
        switch problem {
        case .off:
            StepView(systemImage: "antenna.radiowaves.left.and.right.slash", tint: Theme.error,
                     title: "Bluetooth staat uit",
                     message: "Zet Bluetooth aan in het Bedieningspaneel. Het scherm wordt via Bluetooth beheerd.")
                .accessibilityIdentifier("step.bluetoothOff")
        case .unauthorized:
            StepView(systemImage: "lock.shield", tint: Theme.error, title: "Geen toegang tot Bluetooth",
                     message: "Sta Bluetooth toe in Instellingen om het scherm te kunnen vinden.") {
                Button("Open Instellingen") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.top, 12)
            }
            .accessibilityIdentifier("step.bluetoothUnauthorized")
        case .unsupported:
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.error,
                     title: "Bluetooth niet beschikbaar",
                     message: "Deze telefoon ondersteunt geen Bluetooth LE, en dat is nodig om het scherm te beheren.")
        }
    }
}
#endif
