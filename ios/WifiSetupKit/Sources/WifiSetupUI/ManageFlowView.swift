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
        VStack(spacing: 0) {
            TopBar {
                if client.phase == .choosing {
                    Button { client.backToStatus() } label: {
                        Label("Status", systemImage: "chevron.left")
                    }
                    .accessibilityIdentifier("button.backToStatus")
                } else if let close {
                    Button("Klaar", action: close)
                }
            } trailing: {
                switch client.phase {
                case .status:
                    if client.isRefreshing {
                        Spinner().frame(height: 40)
                    } else {
                        Button {
                            Task { await client.refreshStatus() }
                        } label: {
                            Label("Vernieuwen", systemImage: "arrow.clockwise")
                        }
                        .disabled(!client.isConnected)
                        .accessibilityIdentifier("button.refresh")
                    }
                case .choosing:
                    RescanButton(scanning: client.isScanning) { client.rescan() }
                default:
                    DeviceLabel(text: title)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .animation(.smooth(duration: 0.25), value: client.phase)
        }
        .background(Theme.background.ignoresSafeArea())
        .tint(Theme.accent)
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var content: some View {
        switch client.phase {
        case let .searching(slow):
            SearchingView(slow: slow,
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
                        .buttonStyle(SecondaryButtonStyle())
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
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if client.isReconnecting {
                    HStack(spacing: 10) {
                        Spinner()
                        Text("Verbinding met het scherm herstellen…")
                            .font(Theme.font(15))
                            .foregroundStyle(Theme.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
                    .card()
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("label.reconnecting")
                }
                if let status = client.status {
                    OnlineHeader(status: status, localName: client.link.localName)
                    section("Netwerk") {
                        row("Wifi", value: wifiText(status), id: "label.network", mono: true) {
                            if let signal = status.signal, status.ssid != nil {
                                Image(systemName: "wifi", variableValue: Double(signal) / 100)
                                    .foregroundStyle(Theme.soft)
                            }
                        }
                        if status.network == "ethernet" {
                            row("Kabel", value: "Verbonden", id: "label.ethernet")
                        }
                        row("IP-adres", value: status.ip ?? "Geen", id: "label.ip", mono: true, last: true)
                    }
                    section("Scherm") {
                        row("Toont", value: screenText(status.screenState), id: "label.screen")
                        row("Gekoppeld", value: status.paired.map { $0 ? "Ja" : "Nee" } ?? "Onbekend", id: "label.paired")
                        row("Spelerversie", value: status.agentVersion ?? "Onbekend", id: "label.version", mono: true,
                            last: status.device == nil)
                        if let device = status.device {
                            row("Apparaat", value: device, id: "label.device", last: true)
                        }
                    }
                    if let job = status.job, job.op == "connect", let msg = job.msg {
                        section("Laatste wifi-wijziging") {
                            Text(msg)
                                .font(Theme.font(15))
                                .foregroundStyle(job.state == "failed" ? Theme.error : Theme.muted)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(18)
                                .accessibilityIdentifier("label.job")
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .refreshable { await client.refreshStatus() }
        .accessibilityIdentifier("step.status")
        .safeAreaInset(edge: .bottom) {
            if client.status?.canChangeWifi == true {
                Button { client.chooseNetwork() } label: {
                    Label("Wifi wijzigen", systemImage: "wifi")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(!client.isConnected)
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .background(Theme.background)
                .accessibilityIdentifier("button.changeWifi")
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Kicker(text: title).padding(.horizontal, 4)
            VStack(spacing: 0) { content() }.card()
        }
    }

    private func row(_ label: String, value: String, id: String, mono: Bool = false, last: Bool = false) -> some View {
        row(label, value: value, id: id, mono: mono, last: last) { EmptyView() }
    }

    private func row<Accessory: View>(_ label: String, value: String, id: String, mono: Bool = false, last: Bool = false,
                                      @ViewBuilder accessory: () -> Accessory) -> some View {
        HStack(spacing: 8) {
            Text(label).font(Theme.font(16)).foregroundStyle(Theme.muted)
            Spacer()
            Text(value)
                .font(mono ? Theme.mono(15) : Theme.font(16))
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            accessory()
        }
        .padding(.horizontal, 18)
        .frame(minHeight: 54)
        .overlay(alignment: .bottom) {
            if !last { Rectangle().fill(Theme.line).frame(height: 1) }
        }
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

/// The screen's name in Impact with its online state as a chip (canvas "App scherm beheren").
private struct OnlineHeader: View {
    let status: ManageStatus
    let localName: String

    private var online: Bool? { status.online }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(status.name ?? localName)
                .impactTitle(56)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("label.screenName")
            Chip(text: online == true ? "Online" : online == false ? "Offline: de server is niet bereikbaar" : "Online-status onbekend",
                 tone: online == true ? Theme.ok : online == false ? Theme.error : Theme.muted)
                .accessibilityIdentifier("label.online")
        }
        .padding(.horizontal, 4)
    }
}

private struct ManageNetworkListView: View {
    let client: ManageClient
    @State private var choice: NetworkListView.Choice?

    var body: some View {
        NetworkPicker(title: "Wifi wijzigen", subtitle: "Kies het nieuwe wifi-netwerk voor het scherm",
                      notice: client.notice, networks: client.networks, isScanning: client.isScanning,
                      current: client.status?.ssid, choose: { choice = .init(network: $0) }) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "info.circle")
                    .font(.system(size: 18))
                    .foregroundStyle(Theme.muted)
                Text("Lukt het niet, dan gaat het scherm terug naar het netwerk dat het nu heeft.")
                    .font(Theme.font(14))
                    .lineSpacing(3)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .card()
            .padding(.horizontal, -4)
        }
        .refreshable {
            client.rescan()
            while client.isScanning {
                try? await Task.sleep(for: .milliseconds(300))
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
            StepView(systemImage: "antenna.radiowaves.left.and.right.slash", tint: Theme.warn,
                     title: "Bluetooth staat uit",
                     message: "Zet Bluetooth aan in het Bedieningspaneel. Het scherm wordt via Bluetooth beheerd.")
                .accessibilityIdentifier("step.bluetoothOff")
        case .unauthorized:
            StepView(systemImage: "lock.shield", tint: Theme.warn, title: "Geen toegang tot Bluetooth",
                     message: "Sta Bluetooth toe in Instellingen om het scherm te kunnen vinden.") {
                Button("Open Instellingen") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.top, 12)
            }
            .accessibilityIdentifier("step.bluetoothUnauthorized")
        case .unsupported:
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.warn,
                     title: "Bluetooth niet beschikbaar",
                     message: "Deze telefoon ondersteunt geen Bluetooth LE, en dat is nodig om het scherm te beheren.")
        }
    }
}
#endif
