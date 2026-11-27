#if os(iOS)
import CoreText
import SwiftUI

/// House style D3, the same tokens as the CMS beheer and the TV screens (pi_player/screens.py).
enum Theme {
    static let background = hex(0x0C0C11)
    static let surface = hex(0x16161D)
    static let ghost = hex(0x1E1E26)
    static let line = hex(0x22222B)
    static let line2 = hex(0x2A2A34)
    static let text = Color.white
    static let soft = hex(0xD4D4DC)
    static let muted = hex(0xA9A9B6)
    static let faint = hex(0x8A8A98)
    static let dim = hex(0x6E6E7C)
    /// Brand and live only, never for errors.
    static let accent = hex(0xFF0080)
    static let ok = hex(0x4ADE9B)
    static let busy = hex(0xA3AEFF)
    static let warn = hex(0xFFC24D)
    static let error = hex(0xFF7A66)

    private static func hex(_ value: UInt32) -> Color {
        Color(red: Double(value >> 16 & 0xFF) / 255, green: Double(value >> 8 & 0xFF) / 255,
              blue: Double(value & 0xFF) / 255)
    }

    enum Weight { case regular, medium, bold }

    /// GT Walsheim Pro for the UI. Sizes follow Dynamic Type relative to `style`.
    static func font(_ size: CGFloat, _ weight: Weight = .regular, relativeTo style: Font.TextStyle = .body) -> Font {
        Fonts.register()
        let name = switch weight {
        case .regular: "GTWalsheimPro-Regular"
        case .medium: "GTWalsheimPro-Medium"
        case .bold: "GTWalsheimPro-Bold"
        }
        return .custom(name, size: size, relativeTo: style)
    }

    /// ZZ Impact for the big uppercase headings.
    static func impact(_ size: CGFloat) -> Font {
        Fonts.register()
        return .custom("Impact", size: size, relativeTo: .largeTitle)
    }

    /// Geist Mono for codes, ids and kickers.
    static func mono(_ size: CGFloat, relativeTo style: Font.TextStyle = .body) -> Font {
        Fonts.register()
        return .custom("GeistMono-Medium", size: size, relativeTo: style)
    }
}

/// Registers the bundled fonts once, for the app and the App Clip alike.
enum Fonts {
    private static let registered: Void = {
        let urls = Bundle.module.urls(forResourcesWithExtension: "ttf", subdirectory: "Fonts") ?? []
        CTFontManagerRegisterFontURLs(urls as CFArray, .process, true, nil)
    }()

    static func register() {
        _ = registered
    }
}

/// White pill on ink, the main action (canvas `btn-w`).
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.font(17, .medium, relativeTo: .headline))
            .frame(maxWidth: .infinity, minHeight: 56)
            .padding(.horizontal, 20)
            .foregroundStyle(Theme.background)
            .background(Color.white.opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : 0.35), in: Capsule())
    }
}

/// Outlined pill on ink (canvas `btn-g`); `compact` for the small header buttons.
struct SecondaryButtonStyle: ButtonStyle {
    var compact = false
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.font(compact ? 15 : 17, .medium, relativeTo: compact ? .subheadline : .headline))
            .frame(maxWidth: compact ? nil : .infinity, minHeight: compact ? 40 : 56)
            .padding(.horizontal, compact ? 16 : 20)
            .foregroundStyle(Theme.text.opacity(isEnabled ? 1 : 0.4))
            .background(Theme.ghost.opacity(configuration.isPressed ? 0.6 : 1), in: Capsule())
            .overlay(Capsule().strokeBorder(Theme.line2, lineWidth: 1))
    }
}

extension View {
    /// Surface card with a hairline (canvas `card`).
    func card(radius: CGFloat = 20) -> some View {
        background(Theme.surface, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(Theme.line, lineWidth: 1))
    }
}

extension View {
    /// Big uppercase heading in ZZ Impact. The canvas sets line-height .95; SwiftUI ignores a negative
    /// lineSpacing, so lines keep the font's own leading.
    func impactTitle(_ size: CGFloat) -> some View {
        font(Theme.impact(size)).textCase(.uppercase)
    }
}

/// Mono uppercase label (canvas `kicker`).
struct Kicker: View {
    var text: String

    var body: some View {
        Text(text.uppercased())
            .font(Theme.mono(12, relativeTo: .caption))
            .tracking(1.44)
            .foregroundStyle(Theme.muted)
    }
}

/// Status pill with a dot (canvas `chip`); tone colours as the CMS.
struct Chip: View {
    var text: String
    var tone: Color

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(tone).frame(width: 7, height: 7)
            Text(text)
        }
        .font(Theme.font(13, .medium, relativeTo: .footnote))
        .foregroundStyle(tone)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(tone == Theme.muted ? Theme.ghost : tone.opacity(0.15), in: Capsule())
    }
}

/// Ring with a magenta arc (canvas `spin`).
struct Spinner: View {
    var size: CGFloat = 22
    var lineWidth: CGFloat = 3
    @State private var turning = false

    var body: some View {
        ZStack {
            Circle().stroke(Theme.line2, lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: 0.25)
                .stroke(Theme.accent, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(turning ? 360 : 0))
                .animation(.linear(duration: 0.9).repeatForever(autoreverses: false), value: turning)
        }
        .frame(width: size, height: size)
        .onAppear { turning = true }
        .accessibilityLabel("Bezig")
    }
}

/// The D3 mark: magenta z-blob, white z and (optionally) the TV sticker, white with ink on dark.
struct LogoMark: View {
    var sticker = true

    var body: some View {
        Canvas { context, size in
            let width: CGFloat = sticker ? 60 : 57
            let scale = min(size.width / width, size.height / 48)
            context.scaleBy(x: scale, y: scale)
            context.fill(LogoPath.blob, with: .color(Theme.accent))
            context.fill(LogoPath.zed, with: .color(.white))
            guard sticker else { return }
            let tag = Path(roundedRect: CGRect(x: 39, y: 31, width: 19, height: 12), cornerRadius: 3.5)
            context.fill(tag, with: .color(.white))
            context.stroke(tag, with: .color(Theme.background), lineWidth: 1.5)
            context.draw(Text("TV").font(.custom("GTWalsheimPro-Bold", fixedSize: 8.5)).foregroundStyle(Theme.background),
                         at: CGPoint(x: 48.5, y: 40.2), anchor: UnitPoint(x: 0.5, y: 0.78))
        }
        .aspectRatio(sticker ? 60 / 48 : 57 / 48, contentMode: .fit)
        .onAppear { Fonts.register() }
        .accessibilityHidden(true)
    }
}

/// The logo's SVG paths (canvas board "Main"), relative moves and cubics only.
enum LogoPath {
    static let blob = parse("m49.828 9.4869c-6.4792-5.493-15.954-10.244-27.799-9.333-1.3267 0.1021-2.6662 0.25462-4.0154 0.4632-20.703 3.197-22.511 28.814-10.768 40.89 6.4852 6.6703 17.102 9.2108 30.955 1.5644 4.1535-2.2928 7.5544-4.6361 10.281-6.9866 12.181-10.5 9.5797-19.617 1.3457-26.598z")
    static let zed = parse("m36.892 26.466c-2.2008-1.5402-6.0661-0.072-8.4833 0.2687 3.0896-2.9715 11.332-6.2127 8.0968-9.9872-3.8171-4.6348-14.86-3.3398-17.177-0.5449-0.8701 1.0491-1.0906 2.999 0.2728 3.8168 2.7266 1.9085 8.8074-0.2728 8.8074-0.2728-3.5447 2.9994-11.261 5.7257-9.3527 10.088 0.9167 1.9483 3.0877 2.9301 5.6994 3.2352 4.7779 0.7829 10.79-0.8337 12.409-2.7868 0.87-1.0491 1.0908-2.999-0.2725-3.8171z")

    private static func parse(_ d: String) -> Path {
        var numbers: [CGFloat] = []
        var path = Path()
        var point = CGPoint.zero
        var command: Character = "m"
        var token = ""

        func flushToken() {
            if let value = Double(token) { numbers.append(CGFloat(value)) }
            token = ""
        }

        func flushNumbers() {
            if command == "m", numbers.count >= 2 {
                point = CGPoint(x: point.x + numbers[0], y: point.y + numbers[1])
                path.move(to: point)
                numbers.removeFirst(2)
                command = "l"
            }
            while command == "c", numbers.count >= 6 {
                let n = numbers
                path.addCurve(to: CGPoint(x: point.x + n[4], y: point.y + n[5]),
                              control1: CGPoint(x: point.x + n[0], y: point.y + n[1]),
                              control2: CGPoint(x: point.x + n[2], y: point.y + n[3]))
                point = CGPoint(x: point.x + n[4], y: point.y + n[5])
                numbers.removeFirst(6)
            }
        }

        for char in d {
            if char.isLetter {
                flushToken()
                flushNumbers()
                if char == "z" { path.closeSubpath() }
                command = char
            } else if char == "-" {
                flushToken()
                token = "-"
            } else if char == " " || char == "," {
                flushToken()
            } else {
                token.append(char)
            }
            if !char.isLetter, numbers.count >= 6 { flushNumbers() }
        }
        flushToken()
        flushNumbers()
        return path
    }
}

/// Wordmark: ZEKER bold, ZICHTBAAR regular, 0.14em tracking, stacked as on the app board.
struct Wordmark: View {
    var size: CGFloat = 13

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("ZEKER").font(Theme.font(size, .bold))
            Text("ZICHTBAAR").font(Theme.font(size))
        }
        .tracking(size * 0.14)
        .foregroundStyle(Theme.text)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Zeker Zichtbaar")
    }
}

/// Header row of every step: a small pill on the left, something on the right.
struct TopBar<Leading: View, Trailing: View>: View {
    @ViewBuilder var leading: Leading
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack {
            leading
            Spacer(minLength: 12)
            trailing
        }
        .frame(minHeight: 40)
        .buttonStyle(SecondaryButtonStyle(compact: true))
    }
}

/// Mono, uppercase id of the screen ("SCHERM 1E2F").
struct DeviceLabel: View {
    var text: String

    var body: some View {
        Text(text.uppercased())
            .font(Theme.mono(13, relativeTo: .footnote))
            .tracking(1.04)
            .foregroundStyle(Theme.muted)
            .lineLimit(1)
    }
}

/// An icon, an Impact heading and muted text, left-aligned as on the app boards; the base of every non-list step.
struct StepView<Accessory: View>: View {
    var systemImage: String
    var tint: Color = Theme.ok
    var busy = false
    var title: String
    var message: String?
    @ViewBuilder var accessory: Accessory

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                stack.frame(minHeight: geometry.size.height)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .accessibilityElement(children: .contain)
    }

    private var stack: some View {
        VStack(alignment: .leading, spacing: 28) {
            icon.accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 12) {
                Text(title)
                    .impactTitle(52)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                if let message {
                    Text(message)
                        .font(Theme.font(17))
                        .lineSpacing(4)
                        .foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            accessory
        }
        .frame(maxWidth: 520, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 24)
        .padding(.vertical, 32)
    }

    @ViewBuilder private var icon: some View {
        if busy {
            Spinner(size: 64, lineWidth: 6)
        } else if tint == Theme.ok {
            Image(systemName: systemImage.replacingOccurrences(of: ".circle.fill", with: ""))
                .font(.system(size: 30, weight: .bold))
                .foregroundStyle(Theme.background)
                .frame(width: 64, height: 64)
                .background(Theme.ok, in: Circle())
        } else {
            Image(systemName: systemImage)
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 72, height: 72)
                .background(tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        }
    }
}

extension StepView where Accessory == EmptyView {
    init(systemImage: String, tint: Color = Theme.ok, busy: Bool = false, title: String, message: String?) {
        self.init(systemImage: systemImage, tint: tint, busy: busy, title: title, message: message) { EmptyView() }
    }
}
#endif
