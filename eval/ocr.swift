// OCR an image with Apple's Vision framework and print the recognized text.
//
// Tall page screenshots are split into horizontal strips so Vision doesn't
// downscale them. Vision still occasionally skips a whole line depending on
// where the strip is cut, so the page is read twice, with strip boundaries
// offset by half a strip, and the two readings are merged by position: a line
// is lost only if both readings miss it. Observations clipped by a strip edge
// are ignored (the other reading sees them whole). The page is first cropped to
// its text column: on wide screenshots with large margins Vision skips short
// tokens such as "20%" that it reads reliably once the margins are removed.
//
// Build: swiftc -O eval/ocr.swift -o eval/.bin/ocr
// Usage: eval/.bin/ocr <image-path>

import AppKit
import Foundation
import Vision

let stripHeight = 1600

struct Line {
    let y: Double      // vertical center, pixels from the top of the page
    let x: Double      // left edge, pixels
    let right: Double  // right edge, pixels
    let height: Double
    let text: String
}

/// Lines Vision finds in one strip, in page coordinates, skipping any that touch the strip's cut edges.
/// Horizontal extent of the page's text: the range holding 99% of dark pixels, padded.
func textColumn(_ image: CGImage) -> (Int, Int) {
    let scale = 4
    let w = image.width / scale, h = image.height / scale
    guard w > 0, h > 0,
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                              space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue),
          let data = ctx.data
    else { return (0, image.width) }
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    let px = data.bindMemory(to: UInt8.self, capacity: w * h)
    var columnInk = [Int](repeating: 0, count: w)
    for y in 0..<h { for x in 0..<w where px[y * w + x] < 160 { columnInk[x] += 1 } }
    let total = columnInk.reduce(0, +)
    guard total > 0 else { return (0, image.width) }
    var acc = 0, lo = 0, hi = w - 1
    for x in 0..<w { acc += columnInk[x]; if acc > total / 200 { lo = x; break } }
    acc = 0
    for x in stride(from: w - 1, through: 0, by: -1) { acc += columnInk[x]; if acc > total / 200 { hi = x; break } }
    let pad = 40
    return (max(0, lo * scale - pad), min(image.width, (hi + 1) * scale + pad))
}

func readStrip(_ image: CGImage, top: Int, height: Int, column: (Int, Int)) throws -> [Line] {
    let rect = CGRect(x: column.0, y: top, width: column.1 - column.0, height: height)
    guard let strip = image.cropping(to: rect) else { return [] }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(cgImage: strip, options: [:]).perform([request])
    let h = Double(height), w = Double(column.1 - column.0)
    let cutTop = top > 0, cutBottom = top + height < image.height
    return (request.results ?? []).compactMap { o in
        guard let text = o.topCandidates(1).first?.string else { return nil }
        let b = o.boundingBox  // normalized, origin bottom-left
        let boxTop = (1 - Double(b.maxY)) * h, boxBottom = (1 - Double(b.minY)) * h
        if (cutTop && boxTop < 4) || (cutBottom && boxBottom > h - 4) { return nil }
        return Line(y: Double(top) + (boxTop + boxBottom) / 2, x: Double(column.0) + Double(b.minX) * w,
                    right: Double(column.0) + Double(b.maxX) * w, height: boxBottom - boxTop, text: text)
    }
}

func readPage(_ image: CGImage, offset: Int, column: (Int, Int)) throws -> [Line] {
    var lines: [Line] = []
    var top = offset > 0 ? -offset : 0
    while top < image.height {
        let start = max(top, 0)
        let end = min(top + stripHeight, image.height)
        lines += try readStrip(image, top: start, height: end - start, column: column)
        top += stripHeight
    }
    return lines
}

guard CommandLine.arguments.count == 2,
      let nsImage = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let image = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil)
else {
    FileHandle.standardError.write("usage: ocr <image-path>\n".data(using: .utf8)!)
    exit(1)
}

// Two readings with boundaries offset by half a strip; each also covers the
// other's cut lines, so overlapping strips aren't needed within a reading.
let column = textColumn(image)
var merged = try readPage(image, offset: 0, column: column)
for line in try readPage(image, offset: stripHeight / 2, column: column) {
    // Same text line (vertical centers close) and overlapping horizontally
    let duplicate = merged.contains {
        abs($0.y - line.y) < max($0.height, line.height) * 0.6 && $0.x < line.right && line.x < $0.right
    }
    if !duplicate { merged.append(line) }
}

// Reading order: top-to-bottom, left-to-right within a line.
merged.sort { a, b in
    abs(a.y - b.y) > min(a.height, b.height) * 0.5 ? a.y < b.y : a.x < b.x
}
print(merged.map(\.text).joined(separator: "\n"))
