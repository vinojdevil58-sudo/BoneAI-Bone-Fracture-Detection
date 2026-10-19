function showHelp(topic) {
  const answers = {
    upload: "Open Detect fracture from the sidebar, choose a supported PNG/JPG/JPEG/WEBP image under 12 MB, then select Run AI analysis.",
    model: "BoneAI loads the YOLO weights from models/best.pt using Ultralytics. The labels and confidence values come from your trained model; the training script alone is not the trained weights.",
    safety: "Treat every output as an AI prediction, not a diagnosis. A no-detection result does not rule out a fracture. A qualified medical professional must review the original image."
  };
  const node = document.getElementById("help-answer");
  if (node) node.textContent = answers[topic] || "Please check the project README.";
}
document.addEventListener("change", function(e) {
  if (e.target && e.target.matches('input[type="file"]') && e.target.files.length) {
    const file = e.target.files[0];
    const label = e.target.closest(".upload-zone");
    if (label) {
      const strong = label.querySelector("strong");
      if (strong) strong.textContent = file.name;
    }
    if (e.target.name === "xray" && file.type.startsWith("image/")) {
      const wrap = document.getElementById("xray-preview-wrap");
      const preview = document.getElementById("xray-preview");
      const filename = document.getElementById("xray-filename");
      if (wrap && preview) {
        if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
        const objectUrl = URL.createObjectURL(file);
        preview.src = objectUrl;
        preview.dataset.objectUrl = objectUrl;
        if (filename) filename.textContent = file.name + " · " + (file.size / 1024 / 1024).toFixed(2) + " MB";
        wrap.hidden = false;
      }
    }
  }
});
