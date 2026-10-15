import Foundation
import Vision
import AppKit

// 离线 OCR 工具：基于 Apple Vision 框架
// 用法：ocr_helper <image1> [image2] ...
// 输出：stdout 输出 JSON，结构与字段见下方说明。
//
// 输出 JSON 格式：
// {
//   "results": [
//     {
//       "path": "<输入图片路径>",
//       "width": 1080,
//       "height": 2400,
//       "observations": [
//         {
//           "text": "叫我大猫",
//           "confidence": 0.99,
//           "box": {"x1": 357, "y1": 136, "x2": 584, "y2": 166}
//         }
//       ]
//     }
//   ]
// }
//
// 坐标说明：box 使用归一化坐标 *1000 取整，左上角为 (0,0)，右下角为 (1000,1000)，
// 与图片原始分辨率无关，便于跨设备稳定提取。

// MARK: - 数据结构

struct Box: Encodable {
    var x1: Int
    var y1: Int
    var x2: Int
    var y2: Int
}

struct Observation: Encodable {
    var text: String
    var confidence: Double
    var box: Box
}

struct ImageResult: Encodable {
    var path: String
    var width: Int
    var height: Int
    var observations: [Observation]
}

struct Output: Encodable {
    var results: [ImageResult]
}

// MARK: - 单张图片 OCR

func recognize(path: String) -> ImageResult? {
    guard let image = NSImage(contentsOfFile: path) else {
        FileHandle.standardError.write("无法读取图片: \(path)\n".data(using: .utf8)!)
        return nil
    }
    guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("无法转换图片为 CGImage: \(path)\n".data(using: .utf8)!)
        return nil
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        FileHandle.standardError.write("OCR 失败 \(path): \(error)\n".data(using: .utf8)!)
        return nil
    }

    var observations: [Observation] = []
    for result in request.results ?? [] {
        guard let candidate = result.topCandidates(1).first else { continue }
        let b = result.boundingBox
        // Vision 坐标系：原点在左下角，y 向上。转换为左上角原点、千分比。
        let x1 = max(0, min(1000, Int((b.minX * 1000).rounded())))
        let x2 = max(0, min(1000, Int((b.maxX * 1000).rounded())))
        let y1 = max(0, min(1000, Int(((1 - b.maxY) * 1000).rounded())))
        let y2 = max(0, min(1000, Int(((1 - b.minY) * 1000).rounded())))
        observations.append(Observation(
            text: candidate.string,
            confidence: Double(candidate.confidence),
            box: Box(x1: x1, y1: y1, x2: x2, y2: y2)
        ))
    }

    // 按纵向位置从上到下排序，同一行内从左到右
    observations.sort { a, b in
        if abs(a.box.y1 - b.box.y1) > 15 {
            return a.box.y1 < b.box.y1
        }
        return a.box.x1 < b.box.x1
    }

    return ImageResult(
        path: path,
        width: cgImage.width,
        height: cgImage.height,
        observations: observations
    )
}

// MARK: - main

let args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty {
    FileHandle.standardError.write("用法: ocr_helper <image1> [image2 ...]\n".data(using: .utf8)!)
    exit(2)
}

var results: [ImageResult] = []
for path in args {
    if let r = recognize(path: path) {
        results.append(r)
    }
}

let output = Output(results: results)
let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
do {
    let data = try encoder.encode(output)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
} catch {
    FileHandle.standardError.write("序列化失败: \(error)\n".data(using: .utf8)!)
    exit(1)
}
