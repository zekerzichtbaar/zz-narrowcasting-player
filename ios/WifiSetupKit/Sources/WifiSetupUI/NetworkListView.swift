#if os(iOS)
import SwiftUI
import WifiSetupKit

struct NetworkListView: View {
    let client: SetupClient
    @State private var choice: Choice?

    struct Choice: Identifiable {
        var network: WifiNetwork?
        var id: String { network?.ssid ?? "\u{0}other" }
    }

    var body: some View {
        NetworkPicker(title: "Wifi instellen", subtitle: "Kies het wifi-netwerk voor het scherm",
                      notice: client.notice, networks: client.networks, isScanning: client.isScanning,
                      choose: { choice = Choice(network: $0) }) {
            Text("Trek de lijst omlaag om opnieuw te zoeken.")
                .font(Theme.font(14))
                .foregroundStyle(Theme.faint)
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

/// Impact heading, the networks in one card and a footer (canvas "App netwerk kiezen"); shared by setup and management.
struct NetworkPicker<Footer: View>: View {
    let title: String
    let subtitle: String
    let notice: String?
    let networks: [WifiNetwork]
    let isScanning: Bool
    var current: String?
    let choose: (WifiNetwork?) -> Void
    @ViewBuilder var footer: Footer

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(title)
                        .impactTitle(48)
                        .foregroundStyle(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    Text(subtitle)
                        .font(Theme.font(16))
                        .foregroundStyle(Theme.muted)
                }
                .padding(.horizontal, 4)

                if let notice {
                    Label(notice, systemImage: "exclamationmark.triangle.fill")
                        .font(Theme.font(15))
                        .foregroundStyle(Theme.error)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                        .background(Theme.error.opacity(0.15), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                        .accessibilityIdentifier("label.notice")
                }

                VStack(spacing: 0) {
                    ForEach(networks) { network in
                        Button { choose(network) } label: {
                            NetworkRow(network: network, current: network.ssid == current)
                        }
                        .accessibilityIdentifier("network.\(network.ssid)")
                        Separator()
                    }
                    if networks.isEmpty {
                        Text(isScanning ? "Netwerken zoeken…" : "Geen netwerken gevonden. Trek omlaag om opnieuw te zoeken.")
                            .font(Theme.font(16))
                            .foregroundStyle(Theme.muted)
                            .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
                            .padding(.horizontal, 18)
                        Separator()
                    }
                    Button { choose(nil) } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "plus").font(.system(size: 18, weight: .semibold)).frame(width: 24)
                            Text("Ander netwerk…")
                            Spacer()
                        }
                        .font(Theme.font(17))
                        .foregroundStyle(Theme.muted)
                        .padding(.horizontal, 18)
                        .frame(minHeight: 60)
                        .contentShape(Rectangle())
                    }
                    .accessibilityIdentifier("network.other")
                }
                .buttonStyle(RowButtonStyle())
                .card()
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

                footer.padding(.horizontal, 4)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
    }
}

private struct Separator: View {
    var body: some View {
        Rectangle().fill(Theme.line).frame(height: 1)
    }
}

/// A list row that dims while pressed.
struct RowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Theme.ghost : .clear)
    }
}

struct NetworkRow: View {
    let network: WifiNetwork
    var current = false

    /// Strong signal white, medium soft, weak faint (canvas).
    private var signalColor: Color {
        network.signal >= 67 ? Theme.text : network.signal >= 34 ? Theme.soft : Theme.faint
    }

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "wifi", variableValue: Double(network.signal) / 100)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(signalColor)
                .frame(width: 24)
            Text(network.ssid)
                .font(Theme.font(17))
                .foregroundStyle(Theme.text)
                .lineLimit(1)
            Spacer()
            if current {
                Image(systemName: "checkmark")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Theme.ok)
            }
            if network.secured {
                Image(systemName: "lock")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.faint)
            }
            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Theme.dim)
        }
        .padding(.horizontal, 18)
        .frame(minHeight: 60)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(network.ssid)
        .accessibilityValue("\(network.secured ? "beveiligd" : "open"), signaal \(network.signal) procent")
    }
}

/// Network name and password (canvas "App wachtwoord").
struct PasswordSheet: View {
    let network: WifiNetwork?
    let connect: (String, String) -> Void

    @State private var ssid = ""
    @State private var password = ""
    @State private var reveal = false
    @State private var tried = false
    @FocusState private var focus: Field?
    @Environment(\.dismiss) private var dismiss

    enum Field { case ssid, password }

    private var secured: Bool { network?.secured ?? false }
    private var name: String { network?.ssid ?? ssid.trimmingCharacters(in: .whitespaces) }
    private var problem: String? {
        if network == nil, let problem = Validation.ssid(ssid) { return problem }
        return Validation.password(password, secured: secured)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TopBar {
                Button("Annuleer") { dismiss() }
            } trailing: {
                EmptyView()
            }
            .padding(.horizontal, 20)
            .padding(.top, 20)

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 8) {
                        Kicker(text: "Netwerk")
                        Text(network?.ssid ?? "Ander netwerk")
                            .impactTitle(44)
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityAddTraits(.isHeader)
                    }
                    .padding(.horizontal, 4)

                    if network == nil {
                        FieldBox(label: "Netwerknaam", focused: focus == .ssid) {
                            TextField("Netwerknaam", text: $ssid)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .focused($focus, equals: .ssid)
                                .submitLabel(.next)
                                .onSubmit { focus = .password }
                                .accessibilityIdentifier("field.ssid")
                        } footer: {
                            Text("Voor een verborgen netwerk: typ de naam precies zoals hij is ingesteld.")
                                .foregroundStyle(Theme.faint)
                        }
                    }
                    if secured || network == nil {
                        FieldBox(label: "Wachtwoord", focused: focus == .password) {
                            Group {
                                if reveal {
                                    TextField("Wachtwoord", text: $password)
                                } else {
                                    SecureField("Wachtwoord", text: $password)
                                }
                            }
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .textContentType(.password)
                            .focused($focus, equals: .password)
                            .submitLabel(.join)
                            .onSubmit(submit)
                            .accessibilityIdentifier("field.password")
                            Button(reveal ? "Verberg wachtwoord" : "Toon wachtwoord",
                                   systemImage: reveal ? "eye.slash" : "eye") { reveal.toggle() }
                                .labelStyle(.iconOnly)
                                .foregroundStyle(Theme.muted)
                                .frame(width: 44, height: 44)
                                .buttonStyle(.borderless)
                        } footer: {
                            if tried, let problem {
                                Text(problem).foregroundStyle(Theme.error).accessibilityIdentifier("label.problem")
                            } else if network == nil {
                                Text("Laat leeg bij een open netwerk.").foregroundStyle(Theme.faint)
                            }
                        }
                    } else {
                        Text("Dit is een open netwerk zonder wachtwoord.")
                            .font(Theme.font(16))
                            .foregroundStyle(Theme.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(16)
                            .card()
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 24)
            }
            .scrollBounceBehavior(.basedOnSize)

            Button("Verbinden", action: submit)
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, 20)
                .padding(.bottom, 16)
                .accessibilityIdentifier("button.connect")
        }
        .background(Theme.background.ignoresSafeArea())
        .presentationDetents([.large])
        .presentationBackground(Theme.background)
        .tint(Theme.accent)
        .preferredColorScheme(.dark)
        .onAppear { focus = network == nil ? .ssid : (secured ? .password : nil) }
    }

    private func submit() {
        tried = true
        guard problem == nil else { return }
        connect(name, password)
    }
}

/// Label, an ink input with a magenta ring while focused, and a hint (canvas `field`).
private struct FieldBox<Content: View, Footer: View>: View {
    let label: String
    let focused: Bool
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label)
                .font(Theme.font(14, .medium, relativeTo: .subheadline))
                .foregroundStyle(Theme.text)
                .accessibilityHidden(true)
            HStack(spacing: 4) { content }
                .font(Theme.font(18))
                .foregroundStyle(Theme.text)
                .padding(.leading, 16)
                .padding(.trailing, 6)
                .frame(minHeight: 56)
                .background(Theme.background, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(focused ? Theme.accent : Theme.line2, lineWidth: focused ? 2 : 1))
            footer.font(Theme.font(14))
        }
    }
}
#endif
