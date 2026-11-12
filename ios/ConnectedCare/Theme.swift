import SwiftUI

enum Theme {
    static let navy = Color(red: 0.043, green: 0.122, blue: 0.227)      // #0B1F3A
    // Care palette: navy base, deep blue for buttons and text, sky blue for light surfaces,
    // peach for the friendliest actions (support), warm off-white page.
    static let sky = Color(red: 0.118, green: 0.435, blue: 0.722)       // #1E6FB8 deep blue
    static let skyLight = Color(red: 0.357, green: 0.706, blue: 0.898)  // #5BB4E5 sky blue
    static let skySoft = Color(red: 0.357, green: 0.706, blue: 0.898).opacity(0.18)
    static let peach = Color(red: 0.957, green: 0.647, blue: 0.541)     // #F4A58A
    static let peachInk = Color(red: 0.290, green: 0.141, blue: 0.086)  // #4A2416 text on peach
    static let amber = Color(red: 0.96, green: 0.62, blue: 0.04)
    static let red = Color(red: 0.86, green: 0.24, blue: 0.24)
    static let card = Color.white
    static let page = Color(red: 0.984, green: 0.973, blue: 0.957)     // #FBF8F4 warm off-white
    static let border = Color.black.opacity(0.07)
    static let muted = Color(red: 0.42, green: 0.47, blue: 0.55)
}

struct CardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.border))
    }
}

extension View {
    func card() -> some View { modifier(CardModifier()) }
}

struct SyntheticBadge: View {
    var body: some View {
        Text("SYNTHETIC DEMO DATA")
            .font(.system(size: 10, weight: .bold))
            .tracking(0.6)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Theme.amber.opacity(0.15))
            .foregroundColor(Theme.amber)
            .clipShape(Capsule())
    }
}

struct SectionLabel: View {
    let text: String
    let icon: String
    var body: some View {
        Label(text, systemImage: icon)
            .font(.caption.weight(.semibold))
            .foregroundColor(Theme.muted)
            .textCase(.uppercase)
    }
}
