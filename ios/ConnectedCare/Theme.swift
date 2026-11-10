import SwiftUI

enum Theme {
    static let navy = Color(red: 0.043, green: 0.122, blue: 0.227)      // #0B1F3A
    static let teal = Color(red: 0.078, green: 0.722, blue: 0.651)      // #14B8A6
    static let tealSoft = Color(red: 0.078, green: 0.722, blue: 0.651).opacity(0.12)
    static let amber = Color(red: 0.96, green: 0.62, blue: 0.04)
    static let red = Color(red: 0.86, green: 0.24, blue: 0.24)
    static let card = Color.white
    static let page = Color(red: 0.96, green: 0.97, blue: 0.98)
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
