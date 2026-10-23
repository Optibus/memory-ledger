// Ledger Agent — a floating box that stays in front of whatever you are doing and hands
// each message to Claude, one clean session per message. A tiny AppKit app compiled on
// first use by scripts/agent_panel.py into <home>/LedgerAgent.app (an unbundled process
// is never allowed to become the active app on macOS 26, so its window never goes key).
//
// Protocol with agent_panel.py (one process for the whole run, so the window stays put):
//   argv: <icon path> <subtitle>
//   stdout, one line each:
//     Ask\t<JSON string>   the message, JSON-encoded so newlines survive
//     Cancel               stop the reply that is running
//     Done                 Escape / ✕ / close; the panel exits right after
//   stdin, one JSON object per line:
//     {"t":"event","text":…}               something the agent did (a tool, a skill)
//     {"t":"done","text":…,"meta":…}       the answer, and "$0.04 · 12s · model"
//     {"t":"error","text":…}               the reply failed or was cancelled
//   stdin EOF -> the panel closes.
//
// Two sizes. Thin (the default): one line to type in and one status line, like Dora's
// note. Expanded (⌘E or the ⤢ button): the whole exchange scrolls above a taller box, and
// the window resizes from the corner grip. Return sends, Shift-Return is a new line.
import AppKit

let BLUE   = NSColor(srgbRed:  41/255, green: 151/255, blue: 255/255, alpha: 1)   // the UI's --accent
let INDIGO = NSColor(srgbRed:  44/255, green:  34/255, blue: 140/255, alpha: 1)
let TEAL   = NSColor(srgbRed:  20/255, green: 184/255, blue: 166/255, alpha: 1)
let MINT   = NSColor(srgbRed:  52/255, green: 211/255, blue: 153/255, alpha: 1)
let ROSE   = NSColor(srgbRed: 255/255, green: 140/255, blue: 128/255, alpha: 1)
let GLOW: CGFloat = 30, RADIUS: CGFloat = 22
let THIN_W: CGFloat = 660, THIN_H: CGFloat = 132
let BIG_W: CGFloat = 820, BIG_H: CGFloat = 600
let MIN_BIG = NSSize(width: 520, height: 340)
let HEADER: CGFloat = 46, FOOTER: CGFloat = 34, PAD: CGFloat = 16
let THIN_INPUT: CGFloat = 40, BIG_INPUT: CGFloat = 104

let reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
let reduceTransparency = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency

final class KeyWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class DragView: NSView {
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
}

// Bottom-right grip: drag to resize. A borderless window has no edges to grab.
final class GripView: NSView {
    var minSize = MIN_BIG
    private var start: NSPoint = .zero
    private var startFrame: NSRect = .zero
    override func resetCursorRects() { addCursorRect(bounds, cursor: .crosshair) }
    override func mouseDown(with event: NSEvent) {
        start = NSEvent.mouseLocation
        startFrame = window?.frame ?? .zero
    }
    override func mouseDragged(with event: NSEvent) {
        guard let win = window else { return }
        let p = NSEvent.mouseLocation
        let w = max(minSize.width + 2*GLOW, startFrame.width + (p.x - start.x))
        let h = max(minSize.height + 2*GLOW, startFrame.height - (p.y - start.y))
        // The top edge stays put: the window grows down and right.
        win.setFrame(NSRect(x: startFrame.minX, y: startFrame.maxY - h, width: w, height: h), display: true)
    }
    override func draw(_ r: NSRect) {
        NSColor(white: 1, alpha: 0.45).setStroke()
        for i in 0..<3 {
            let o = CGFloat(i) * 4 + 3
            let p = NSBezierPath()
            p.move(to: NSPoint(x: bounds.maxX - o, y: 2)); p.line(to: NSPoint(x: bounds.maxX - 2, y: o))
            p.lineWidth = 1.2; p.stroke()
        }
    }
}

// A small round button: press feedback on mouse-down, action on mouse-up inside.
final class RoundButton: NSView {
    var onClick: (() -> Void)?
    let glyph = NSTextField(labelWithString: "")
    init(frame: NSRect, symbol: String) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor(white: 0.02, alpha: 0.4).cgColor
        layer?.cornerRadius = frame.height / 2
        glyph.stringValue = symbol
        glyph.alignment = .center
        glyph.textColor = NSColor(white: 1, alpha: 0.92)
        glyph.font = .systemFont(ofSize: 12, weight: .bold)
        glyph.frame = NSRect(x: 0, y: (frame.height - 17) / 2, width: frame.width, height: 17)
        addSubview(glyph)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func mouseDown(with event: NSEvent) { alphaValue = 0.6 }
    override func mouseUp(with event: NSEvent) {
        alphaValue = 1
        if bounds.insetBy(dx: -8, dy: -8).contains(convert(event.locationInWindow, from: nil)) { onClick?() }
    }
}

final class GradientView: NSView {
    let gradient = CAGradientLayer()
    override init(frame: NSRect) {
        super.init(frame: frame)
        layer = gradient
        wantsLayer = true
    }
    required init?(coder: NSCoder) { fatalError() }
}

// The card carries the glow; its shadow path follows it through every resize.
final class CardView: NSView {
    override func layout() {
        super.layout()
        layer?.shadowPath = CGPath(roundedRect: bounds, cornerWidth: RADIUS, cornerHeight: RADIUS, transform: nil)
    }
}

// The input: Return sends, Shift-Return breaks the line, Escape closes.
final class InputView: NSTextView {
    var onSend: (() -> Void)?
    var onEscape: (() -> Void)?
    var placeholder = ""
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 || event.keyCode == 76 {          // Return, keypad Enter
            if event.modifierFlags.contains(.shift) { insertNewlineIgnoringFieldEditor(nil) }
            else { onSend?() }
            return
        }
        if event.keyCode == 53 { onEscape?(); return }
        super.keyDown(with: event)
    }
    override func draw(_ r: NSRect) {
        super.draw(r)
        if string.isEmpty, !placeholder.isEmpty {
            (placeholder as NSString).draw(at: NSPoint(x: textContainerInset.width + 5, y: textContainerInset.height),
                withAttributes: [.foregroundColor: NSColor(white: 1, alpha: 0.55), .font: font ?? .systemFont(ofSize: 14)])
        }
    }
    override func didChangeText() { super.didChangeText(); needsDisplay = true }
}

final class App: NSObject, NSApplicationDelegate {
    var win: KeyWindow!
    var card: CardView!
    var box: NSView!
    var flash: NSView!
    var input: InputView!
    var inputScroll: NSScrollView!
    var output: NSTextView!
    var outputScroll: NSScrollView!
    var status: NSTextField!
    var hints: NSView!
    var grip: GripView!
    var expandButton: RoundButton!
    var expanded = false
    var busy = false
    var bigFrame: NSRect?
    let args = CommandLine.arguments

    func applicationDidFinishLaunching(_ n: Notification) {
        let iconPath = args.count > 1 ? args[1] : ""
        let subtitle = args.count > 2 ? args[2] : ""

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let winW = THIN_W + 2*GLOW, winH = THIN_H + 2*GLOW
        let frame = NSRect(x: screen.midX - winW/2, y: screen.minY + screen.height * 0.62, width: winW, height: winH)
        win = KeyWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
        win.isOpaque = false
        win.backgroundColor = .clear
        win.hasShadow = false
        win.level = .floating
        win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        win.isReleasedWhenClosed = false

        let content = DragView(frame: NSRect(x: 0, y: 0, width: winW, height: winH))
        content.wantsLayer = true
        content.autoresizesSubviews = true
        win.contentView = content

        card = CardView(frame: content.bounds.insetBy(dx: GLOW, dy: GLOW))
        card.autoresizingMask = [.width, .height]
        card.wantsLayer = true
        card.layer?.shadowColor = BLUE.cgColor
        card.layer?.shadowOpacity = 0.6
        card.layer?.shadowRadius = 20
        card.layer?.shadowOffset = CGSize(width: 0, height: -3)
        content.addSubview(card)

        let aurora = GradientView(frame: card.bounds)
        aurora.autoresizingMask = [.width, .height]
        aurora.gradient.colors = [INDIGO.cgColor, BLUE.cgColor, BLUE.cgColor, TEAL.cgColor]
        aurora.gradient.locations = [0, 0.45, 0.7, 1]
        aurora.gradient.startPoint = CGPoint(x: 0, y: 0)
        aurora.gradient.endPoint = CGPoint(x: 1, y: 1)
        aurora.gradient.cornerRadius = RADIUS
        aurora.gradient.masksToBounds = true
        aurora.gradient.borderWidth = 1
        aurora.gradient.borderColor = NSColor(white: 1, alpha: 0.35).cgColor
        card.addSubview(aurora)

        let W = card.bounds.width, H = card.bounds.height

        // Header: icon, name, where Claude runs; expand and close on the right.
        if !iconPath.isEmpty, let img = NSImage(contentsOfFile: iconPath) {
            let iv = NSImageView(frame: NSRect(x: PAD, y: H - 36, width: 24, height: 24))
            iv.image = img
            iv.imageScaling = .scaleProportionallyUpOrDown
            iv.autoresizingMask = [.minYMargin]
            card.addSubview(iv)
        }
        let title = label(NSRect(x: PAD + 32, y: H - 33, width: 120, height: 19), size: 13.5, weight: .semibold)
        title.stringValue = "Ledger Agent"
        title.sizeToFit()
        title.autoresizingMask = [.minYMargin]
        card.addSubview(title)
        let sub = label(NSRect(x: title.frame.maxX + 8, y: H - 32, width: W - title.frame.maxX - 100, height: 17), size: 11.5, weight: .regular)
        sub.stringValue = subtitle
        sub.textColor = NSColor(white: 1, alpha: 0.7)
        sub.lineBreakMode = .byTruncatingMiddle
        sub.autoresizingMask = [.minYMargin, .width]
        card.addSubview(sub)

        let close = RoundButton(frame: NSRect(x: W - PAD - 26, y: H - 37, width: 26, height: 26), symbol: "\u{2715}")
        close.autoresizingMask = [.minXMargin, .minYMargin]
        close.onClick = { [weak self] in self?.finish() }
        card.addSubview(close)
        expandButton = RoundButton(frame: NSRect(x: W - PAD - 26 - 34, y: H - 37, width: 26, height: 26), symbol: "\u{2922}")
        expandButton.autoresizingMask = [.minXMargin, .minYMargin]
        expandButton.onClick = { [weak self] in self?.toggleExpanded() }
        card.addSubview(expandButton)

        // The exchange, above the box. Only shown when expanded.
        outputScroll = NSScrollView(frame: NSRect(x: PAD, y: FOOTER + THIN_INPUT + 10, width: W - 2*PAD, height: 10))
        outputScroll.autoresizingMask = [.width, .height]
        outputScroll.drawsBackground = false
        outputScroll.hasVerticalScroller = true
        outputScroll.scrollerStyle = .overlay
        outputScroll.borderType = .noBorder
        output = NSTextView(frame: outputScroll.bounds)
        output.isEditable = false
        output.isSelectable = true
        output.drawsBackground = false
        output.textContainerInset = NSSize(width: 4, height: 6)
        output.autoresizingMask = [.width]
        output.isVerticallyResizable = true
        output.textContainer?.widthTracksTextView = true
        outputScroll.documentView = output
        outputScroll.isHidden = true
        // A faint dark glass behind the exchange, so long answers stay easy to read.
        outputScroll.wantsLayer = true
        outputScroll.layer?.backgroundColor = NSColor(white: 0.02, alpha: reduceTransparency ? 0.6 : 0.24).cgColor
        outputScroll.layer?.cornerRadius = 12
        output.textContainerInset = NSSize(width: 10, height: 10)
        card.addSubview(outputScroll)

        // Dark glass box with the input in it.
        box = NSView(frame: NSRect(x: PAD, y: FOOTER, width: W - 2*PAD, height: THIN_INPUT))
        box.autoresizingMask = [.width]
        box.wantsLayer = true
        box.layer?.backgroundColor = NSColor(white: 0.03, alpha: reduceTransparency ? 0.9 : 0.55).cgColor
        box.layer?.cornerRadius = 12
        box.layer?.borderWidth = 1
        box.layer?.borderColor = NSColor(white: 1, alpha: reduceTransparency ? 0.4 : 0.22).cgColor
        card.addSubview(box)

        flash = NSView(frame: box.bounds)
        flash.autoresizingMask = [.width, .height]
        flash.wantsLayer = true
        flash.layer?.backgroundColor = MINT.cgColor
        flash.layer?.cornerRadius = 12
        flash.layer?.opacity = 0
        box.addSubview(flash)

        inputScroll = NSScrollView(frame: box.bounds.insetBy(dx: 6, dy: 4))
        inputScroll.autoresizingMask = [.width, .height]
        inputScroll.drawsBackground = false
        inputScroll.hasVerticalScroller = true
        inputScroll.scrollerStyle = .overlay
        inputScroll.borderType = .noBorder
        input = InputView(frame: inputScroll.bounds)
        input.isRichText = false
        input.drawsBackground = false
        input.textColor = .white
        input.insertionPointColor = .white
        input.font = .systemFont(ofSize: 14)
        input.textContainerInset = NSSize(width: 2, height: 7)
        input.autoresizingMask = [.width]
        input.isVerticallyResizable = true
        input.textContainer?.widthTracksTextView = true
        input.allowsUndo = true
        input.isAutomaticQuoteSubstitutionEnabled = false
        input.isAutomaticDashSubstitutionEnabled = false
        input.placeholder = "Ask the Ledger Agent… every message starts a clean session"
        input.onSend = { [weak self] in self?.send() }
        input.onEscape = { [weak self] in self?.finish() }
        inputScroll.documentView = input
        box.addSubview(inputScroll)

        // Footer: keycap hints left, status right.
        hints = NSView(frame: NSRect(x: PAD, y: 0, width: 330, height: FOOTER))
        card.addSubview(hints)
        var hx: CGFloat = 0
        hx = keycap("\u{21B5}", "ask", x: hx)
        hx = keycap("\u{2318}E", "expand", x: hx + 10)
        _ = keycap("esc", "close", x: hx + 10)

        status = label(NSRect(x: PAD + 330, y: 9, width: W - 2*PAD - 330 - 14, height: 17), size: 12, weight: .medium)
        status.alignment = .right
        status.lineBreakMode = .byTruncatingTail
        status.autoresizingMask = [.width]
        status.stringValue = "ready"
        status.textColor = NSColor(white: 1, alpha: 0.75)
        card.addSubview(status)

        grip = GripView(frame: NSRect(x: W - 16, y: 2, width: 14, height: 14))
        grip.autoresizingMask = [.minXMargin]
        grip.isHidden = true
        card.addSubview(grip)

        // Appear: 94% → 100% on a critically damped spring while the window fades up.
        win.alphaValue = 0
        win.makeKeyAndOrderFront(nil)
        win.makeFirstResponder(input)
        if !reduceMotion, let l = content.layer {
            let a = CASpringAnimation(keyPath: "transform.scale")
            a.fromValue = 0.94; a.toValue = 1
            a.stiffness = 320; a.damping = 36; a.duration = a.settlingDuration
            l.add(a, forKey: "in")
        }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = reduceMotion ? 0.12 : 0.2
            self.win.animator().alphaValue = 1
        }
        NSApp.activate(ignoringOtherApps: true)
        listen()

        // Activation from a terminal-launched process can lose the race with the shell's
        // own window; retry a few times until we are actually key.
        var tries = 0
        Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            tries += 1
            if self.win.isKeyWindow || tries > 12 { t.invalidate() }
            else { NSApp.activate(ignoringOtherApps: true); self.win.makeKeyAndOrderFront(nil) }
        }

        // LEDGER_AGENT_SNAP=<prefix>: render to <prefix>-thin.png; with LEDGER_AGENT_SNAP_ASK
        // also ask that, expand, and render <prefix>-expanded.png once the answer is in.
        let env = ProcessInfo.processInfo.environment
        if let prefix = env["LEDGER_AGENT_SNAP"] {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in self?.snap(prefix + "-thin") }
            if let ask = env["LEDGER_AGENT_SNAP_ASK"] {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                    self?.input.string = ask; self?.send()
                }
                snapWhenDone = prefix
            }
        }
    }
    var snapWhenDone: String?

    // ------------------------------------------------------------ sizes
    @objc func toggleExpanded() { setExpanded(!expanded) }

    func setExpanded(_ on: Bool) {
        expanded = on
        let top = win.frame.maxY
        let target: NSRect
        if on {
            let size = bigFrame?.size ?? NSSize(width: BIG_W + 2*GLOW, height: BIG_H + 2*GLOW)
            target = NSRect(x: win.frame.minX, y: top - size.height, width: max(size.width, win.frame.width), height: size.height)
        } else {
            bigFrame = win.frame
            target = NSRect(x: win.frame.minX, y: top - (THIN_H + 2*GLOW), width: THIN_W + 2*GLOW, height: THIN_H + 2*GLOW)
        }
        outputScroll.isHidden = !on
        grip.isHidden = !on
        expandButton.glyph.stringValue = on ? "\u{2921}" : "\u{2922}"
        // The box is pinned to the footer; it is taller when there is room.
        let inputH = on ? BIG_INPUT : THIN_INPUT
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = reduceMotion ? 0 : 0.22
            ctx.allowsImplicitAnimation = true
            self.win.setFrame(target, display: true, animate: !reduceMotion)
        }
        box.frame = NSRect(x: PAD, y: FOOTER, width: card.bounds.width - 2*PAD, height: inputH)
        outputScroll.frame = NSRect(x: PAD, y: FOOTER + inputH + 10, width: card.bounds.width - 2*PAD,
                                    height: max(10, card.bounds.height - HEADER - FOOTER - inputH - 16))
        output.scrollToEndOfDocument(nil)
        win.makeFirstResponder(input)
    }

    // ------------------------------------------------------------ talking
    func send() {
        let text = input.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return }
        if busy { setStatus("still working on the last one · ⌘. to stop it", ROSE); return }
        guard let data = try? JSONSerialization.data(withJSONObject: [text], options: []),
              let arr = String(data: data, encoding: .utf8) else { return }
        // [text] encodes as ["…"]; strip the brackets to get the JSON string alone.
        let encoded = String(arr.dropFirst().dropLast())
        print("Ask\t\(encoded)")
        fflush(stdout)
        input.string = ""
        input.needsDisplay = true
        busy = true
        append(text, as: .you)
        setStatus("new session · thinking…", NSColor(white: 1, alpha: 0.85))
        pulse(true)
    }

    func cancel() {
        guard busy else { return }
        print("Cancel")
        fflush(stdout)
        setStatus("stopping…", ROSE)
    }

    func listen() {
        DispatchQueue.global().async { [weak self] in
            while let line = readLine() {
                guard let data = line.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                DispatchQueue.main.async { self?.handle(obj) }
            }
            DispatchQueue.main.async { self?.finish() }
        }
    }

    func handle(_ m: [String: Any]) {
        let t = m["t"] as? String ?? ""
        let text = m["text"] as? String ?? ""
        switch t {
        case "event":
            append(text, as: .event)
            setStatus(text, NSColor(white: 1, alpha: 0.85))
        case "done":
            busy = false
            pulse(false)
            flashBox(MINT)
            append(text, as: .agent)
            if let meta = m["meta"] as? String, !meta.isEmpty { append(meta, as: .meta) }
            let first = text.split(separator: "\n").first.map(String.init) ?? "done"
            setStatus(expanded ? "done · \(m["meta"] as? String ?? "")" : "\(first)  ·  ⌘E to read", NSColor(white: 1, alpha: 0.95))
            if let prefix = snapWhenDone {
                snapWhenDone = nil
                setExpanded(true)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in self?.snap(prefix + "-expanded") }
            }
        case "error":
            busy = false
            pulse(false)
            flashBox(ROSE)
            append(text, as: .error)
            setStatus(text, ROSE)
        default:
            break
        }
    }

    enum Role { case you, agent, event, meta, error }

    func append(_ s: String, as role: Role) {
        guard let storage = output.textStorage else { return }
        let body = NSFont.systemFont(ofSize: 13.5)
        let para = NSMutableParagraphStyle()
        para.paragraphSpacing = 6
        para.lineSpacing = 2
        var out = NSMutableAttributedString()
        switch role {
        case .you:
            if storage.length > 0 {
                out.append(NSAttributedString(string: "\n", attributes: [.font: NSFont.systemFont(ofSize: 6)]))
            }
            out.append(NSAttributedString(string: "You\n", attributes: [.font: NSFont.systemFont(ofSize: 11, weight: .bold),
                                                                        .foregroundColor: NSColor(white: 1, alpha: 0.6)]))
            out.append(NSAttributedString(string: s + "\n", attributes: [.font: NSFont.systemFont(ofSize: 13.5, weight: .medium),
                                                                         .foregroundColor: NSColor.white, .paragraphStyle: para]))
        case .event:
            out.append(NSAttributedString(string: "  \u{203A} " + s + "\n", attributes: [
                .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                .foregroundColor: NSColor(white: 1, alpha: 0.62)]))
        case .agent:
            out.append(NSAttributedString(string: "Ledger Agent\n", attributes: [.font: NSFont.systemFont(ofSize: 11, weight: .bold),
                                                                                 .foregroundColor: MINT]))
            let opts = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
            let tidy = s.replacingOccurrences(of: "\n{3,}", with: "\n\n", options: .regularExpression)
                        .replacingOccurrences(of: "\n\n", with: "\n")
            if let md = try? NSMutableAttributedString(markdown: tidy, options: opts, baseURL: nil) {
                md.addAttributes([.foregroundColor: NSColor(white: 1, alpha: 0.96), .paragraphStyle: para],
                                 range: NSRange(location: 0, length: md.length))
                // Markdown keeps bold/italic/code as font traits on top of the body size.
                md.enumerateAttribute(.font, in: NSRange(location: 0, length: md.length)) { v, r, _ in
                    let f = v as? NSFont
                    let traits = f?.fontDescriptor.symbolicTraits ?? []
                    var font = body
                    if traits.contains(.monoSpace) { font = .monospacedSystemFont(ofSize: 12.5, weight: .regular) }
                    else if traits.contains(.bold) { font = .systemFont(ofSize: 13.5, weight: .semibold) }
                    if traits.contains(.italic) { font = NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask) }
                    md.addAttribute(.font, value: font, range: r)
                }
                out.append(md)
                out.append(NSAttributedString(string: "\n"))
            } else {
                out.append(NSAttributedString(string: s + "\n", attributes: [.font: body, .foregroundColor: NSColor.white, .paragraphStyle: para]))
            }
        case .meta:
            out.append(NSAttributedString(string: s + "\n", attributes: [.font: NSFont.systemFont(ofSize: 11),
                                                                         .foregroundColor: NSColor(white: 1, alpha: 0.5)]))
        case .error:
            out.append(NSAttributedString(string: s + "\n", attributes: [.font: body, .foregroundColor: ROSE]))
        }
        storage.append(out)
        out = NSMutableAttributedString()
        output.scrollToEndOfDocument(nil)
    }

    func setStatus(_ s: String, _ color: NSColor) {
        status.stringValue = s.replacingOccurrences(of: "\n", with: " ")
        status.textColor = color
    }

    func pulse(_ on: Bool) {
        guard let layer = card.layer else { return }
        if reduceMotion { layer.shadowOpacity = on ? 0.95 : 0.6; return }
        if on {
            let a = CABasicAnimation(keyPath: "shadowOpacity")
            a.fromValue = 0.6; a.toValue = 1.0
            a.duration = 0.6; a.autoreverses = true; a.repeatCount = .infinity
            layer.add(a, forKey: "pulse")
            let r = CABasicAnimation(keyPath: "shadowRadius")
            r.fromValue = 20; r.toValue = 30
            r.duration = 0.6; r.autoreverses = true; r.repeatCount = .infinity
            layer.add(r, forKey: "pulseR")
        } else {
            layer.removeAnimation(forKey: "pulse")
            layer.removeAnimation(forKey: "pulseR")
        }
    }

    func flashBox(_ c: NSColor) {
        flash.layer?.backgroundColor = c.cgColor
        let a = CABasicAnimation(keyPath: "opacity")
        a.fromValue = 0.55; a.toValue = 0
        a.duration = 0.65
        flash.layer?.add(a, forKey: "flash")
    }

    // ------------------------------------------------------------ bits
    func label(_ r: NSRect, size: CGFloat, weight: NSFont.Weight) -> NSTextField {
        let l = NSTextField(frame: r)
        l.isEditable = false; l.isSelectable = false; l.isBordered = false; l.drawsBackground = false
        l.textColor = NSColor(white: 1, alpha: 0.95)
        l.font = .systemFont(ofSize: size, weight: weight)
        return l
    }

    func keycap(_ key: String, _ verb: String, x: CGFloat) -> CGFloat {
        let keyFont = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .semibold)
        let kw = (key as NSString).size(withAttributes: [.font: keyFont]).width + 12
        let cap = NSView(frame: NSRect(x: x, y: 8, width: kw, height: 18))
        cap.wantsLayer = true
        cap.layer?.backgroundColor = NSColor(white: 1, alpha: 0.2).cgColor
        cap.layer?.cornerRadius = 5
        cap.layer?.borderWidth = 1
        cap.layer?.borderColor = NSColor(white: 1, alpha: 0.28).cgColor
        let k = label(NSRect(x: 0, y: 1, width: kw, height: 15), size: 10.5, weight: .semibold)
        k.font = keyFont
        k.alignment = .center
        k.stringValue = key
        cap.addSubview(k)
        hints.addSubview(cap)
        let v = label(NSRect(x: x + kw + 5, y: 8, width: 60, height: 17), size: 11.5, weight: .medium)
        v.stringValue = verb
        v.textColor = NSColor(white: 1, alpha: 0.82)
        hints.addSubview(v)
        return x + kw + 5 + (verb as NSString).size(withAttributes: [.font: v.font!]).width
    }

    func snap(_ path: String) {
        guard let root = win.contentView?.layer else { return }
        let scale: CGFloat = 2
        let w = Int(root.bounds.width * scale), h = Int(root.bounds.height * scale)
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        ctx.setFillColor(NSColor(white: 0.1, alpha: 1).cgColor)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.scaleBy(x: scale, y: scale)
        root.render(in: ctx)
        guard let img = ctx.makeImage() else { return }
        try? NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:])?
            .write(to: URL(fileURLWithPath: "\(path).png"))
    }

    var finishing = false
    @objc func finish() {
        if finishing { return }
        finishing = true
        print("Done")
        fflush(stdout)
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = reduceMotion ? 0.1 : 0.16
            self.win.animator().alphaValue = 0
        }, completionHandler: { NSApp.terminate(nil) })
    }

    @objc func stop(_ s: Any?) { cancel() }
    @objc func expand(_ s: Any?) { toggleExpanded() }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = App()
// An accessory app has no menu bar, so shortcuts had nothing to route through. A hidden
// menu gives the text views ⌘C/⌘V/⌘X/⌘A/⌘Z, and the panel ⌘E (expand) and ⌘. (stop).
let mainMenu = NSMenu()
let editItem = NSMenuItem()
mainMenu.addItem(editItem)
let editMenu = NSMenu(title: "Edit")
editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
let ex = editMenu.addItem(withTitle: "Expand", action: #selector(App.expand(_:)), keyEquivalent: "e")
ex.target = delegate
let st = editMenu.addItem(withTitle: "Stop", action: #selector(App.stop(_:)), keyEquivalent: ".")
st.target = delegate
let cl = editMenu.addItem(withTitle: "Close", action: #selector(App.finish), keyEquivalent: "w")
cl.target = delegate
editItem.submenu = editMenu
app.mainMenu = mainMenu
app.delegate = delegate
app.run()
