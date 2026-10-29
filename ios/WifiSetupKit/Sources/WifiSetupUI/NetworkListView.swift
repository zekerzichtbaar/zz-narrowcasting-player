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
                    Button { choice = Choice(network: network) } label: {
                        NetworkRow(network: network)
                    }
                    .accessibilityIdentifier("network.\(network.ssid)")
                }
                .listRowBackground(Theme.card)
                if client.networks.isEmpty {
                    Text(client.isScanning ? "Netwerken zoeken…" : "Geen netwerken gevonden. Trek omlaag om opnieuw te zoeken.")
                        .foregroundStyle(Theme.muted)
                        .listRowBackground(Theme.card)
                }
                Button { choice = Choice(network: nil) } label: {
                    Label("Ander netwerk…", systemImage: "plus")
                        .foregroundStyle(Theme.accent)
                }
                .listRowBackground(Theme.card)
                .accessibilityIdentifier("network.other")
            } header: {
                Text("Kies het wifi-netwerk voor het scherm")
                    .textCase(nil)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
            } footer: {
                Text("Trek de lijst omlaag om opnieuw te zoeken.")
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

struct NetworkRow: View {
    let network: WifiNetwork

    var body: some View {
        HStack(spacing: 12) {
            Text(network.ssid)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
            Spacer()
            if network.secured {
                Image(systemName: "lock.fill")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
            }
            Image(systemName: "wifi", variableValue: Double(network.signal) / 100)
                .foregroundStyle(Theme.soft)
        }
        .frame(minHeight: 36)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(network.ssid)
        .accessibilityValue("\(network.secured ? "beveiligd" : "open"), signaal \(network.signal) procent")
    }
}

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
        NavigationStack {
            Form {
                if network == nil {
                    Section {
                        TextField("Netwerknaam", text: $ssid)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($focus, equals: .ssid)
                            .submitLabel(.next)
                            .onSubmit { focus = .password }
                            .accessibilityIdentifier("field.ssid")
                    } footer: {
                        Text("Voor een verborgen netwerk: typ de naam precies zoals hij is ingesteld.")
                    }
                }
                if secured || network == nil {
                    Section {
                        HStack {
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
                                .buttonStyle(.borderless)
                        }
                    } footer: {
                        if tried, let problem {
                            Text(problem).foregroundStyle(Theme.error).accessibilityIdentifier("label.problem")
                        } else if network == nil {
                            Text("Laat leeg bij een open netwerk.")
                        }
                    }
                } else {
                    Section {
                        Text("Dit is een open netwerk zonder wachtwoord.").foregroundStyle(Theme.muted)
                    }
                }
                Section {
                    Button("Verbinden", action: submit)
                        .buttonStyle(PrimaryButtonStyle())
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .accessibilityIdentifier("button.connect")
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle(network?.ssid ?? "Ander netwerk")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuleer") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
        .onAppear { focus = network == nil ? .ssid : (secured ? .password : nil) }
    }

    private func submit() {
        tried = true
        guard problem == nil else { return }
        connect(name, password)
    }
}
#endif
