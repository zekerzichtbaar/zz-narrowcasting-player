#if os(iOS)
import SwiftUI

/// The TV screens' palette (pi_player/screens.py, portal.py).
enum Theme {
    static let background = Color(red: 7 / 255, green: 8 / 255, blue: 10 / 255)
    static let text = Color(red: 242 / 255, green: 244 / 255, blue: 246 / 255)
    static let soft = Color(red: 201 / 255, green: 209 / 255, blue: 219 / 255)
    static let muted = Color(red: 163 / 255, green: 171 / 255, blue: 183 / 255)
    static let line = Color(red: 58 / 255, green: 65 / 255, blue: 76 / 255)
    static let accent = Color(red: 61 / 255, green: 220 / 255, blue: 132 / 255)
    static let onAccent = Color(red: 4 / 255, green: 32 / 255, blue: 14 / 255)
    static let error = Color(red: 1, green: 110 / 255, blue: 100 / 255)
    static let card = Color.white.opacity(0.06)
}

struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity, minHeight: 52)
            .foregroundStyle(Theme.onAccent)
            .background(Theme.accent.opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : 0.35),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// A centred icon + title + text block, the base of every non-list step.
struct StepView<Accessory: View>: View {
    var systemImage: String
    var tint: Color = Theme.accent
    var busy = false
    var title: String
    var message: String?
    @ViewBuilder var accessory: Accessory

    var body: some View {
        VStack(spacing: 18) {
            Spacer(minLength: 24)
            ZStack {
                Circle().fill(tint.opacity(0.14)).frame(width: 96, height: 96)
                if busy {
                    ProgressView().controlSize(.large).tint(tint)
                } else {
                    Image(systemName: systemImage)
                        .font(.system(size: 40, weight: .semibold))
                        .foregroundStyle(tint)
                }
            }
            .accessibilityHidden(true)
            Text(title)
                .font(.title2.bold())
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.text)
            if let message {
                Text(message)
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            accessory
            Spacer(minLength: 24)
        }
        .padding(.horizontal, 24)
        .frame(maxWidth: 520)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }
}

extension StepView where Accessory == EmptyView {
    init(systemImage: String, tint: Color = Theme.accent, busy: Bool = false, title: String, message: String?) {
        self.init(systemImage: systemImage, tint: tint, busy: busy, title: title, message: message) { EmptyView() }
    }
}
#endif
