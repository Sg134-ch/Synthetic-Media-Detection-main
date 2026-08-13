import { NextRequest, NextResponse } from "next/server";

export const maxDuration = 60;

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const file = formData.get("file") as File;
    const filename = file?.name || "media_file.png";
    const fileType = file?.type || "image/png";

    // Determine media type
    const isVideo = fileType.startsWith("video/") || filename.endsWith(".mp4") || filename.endsWith(".avi");
    const isAudio = fileType.startsWith("audio/") || filename.endsWith(".mp3") || filename.endsWith(".wav");

    // Forward the file to the Hugging Face / Python backend
    // Use an internal server URL instead of the public one, and read backend API key
    const apiUrl = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000').replace('localhost', '127.0.0.1');
    const apiKey = process.env.BACKEND_API_KEY || '';
    
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 min timeout

      // Next.js consumes the file stream when we call req.formData().
      // To forward it via fetch safely, we must reconstruct it.
      const arrayBuffer = await file.arrayBuffer();
      const newFormData = new FormData();
      newFormData.append("file", new Blob([arrayBuffer], { type: file.type }), filename);

      const backendResponse = await fetch(`${apiUrl}/detect/auto`, {
        method: 'POST',
        headers: {
          'x-api-key': apiKey,
        },
        body: newFormData,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!backendResponse.ok) {
        let errorData = "Unknown error";
        try {
          errorData = await backendResponse.text();
        } catch (e) {}
        console.error("Backend Error:", backendResponse.status, errorData);
        throw new Error(`Backend returned ${backendResponse.status}: ${errorData}`);
      }

      const backendData = await backendResponse.json();

      // Return the actual backend data. 
      // Note: If your Python backend doesn't return the exact JSON structure expected by the UI 
      // (like 'media_type', 'verdict', 'details.detection', etc.), you will need to map it here.
      // For now, we return exactly what the backend gives us, assuming it's built to match.
      return NextResponse.json({
        ...backendData,
        file_info: {
          filename,
          content_type: fileType,
          size_bytes: file?.size || 0
        }
      });
    } catch (fetchError: any) {
      console.error("Backend fetch failed:", fetchError);
      
      return NextResponse.json({
        detail: fetchError.message || "Failed to communicate with backend AI.",
      }, { status: 500 });
    }
  } catch (err: any) {
    return NextResponse.json({ detail: err.message || "Something went wrong" }, { status: 500 });
  }
}
