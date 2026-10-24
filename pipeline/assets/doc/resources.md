# 📊 Resources & Profiling Summary (H800 GPU, 80GB VRAM, 120GB RAM)

This document reports the peak GPU memory usage and runtime of **SAM-Body4D** on the
three example videos included in the Gradio demo.  
All results are measured on an **NVIDIA H800 GPU (80 GB VRAM)** with **120 GB system RAM**.

Peak GPU memory is shown because it determines the **minimum GPU requirement** to avoid
out-of-memory (OOM) issues.  
CPU RAM usage is small relative to the available **120 GB** and is not a limiting factor.


---

## 💡 Recommendation

If your video does **not** contain severe or long-term occlusions, set: `completion.enable = false` in [`configs/body4d.yaml`](../../configs/body4d.yaml). This disables the occlusion-aware diffusion refinement module and can **significantly improve inference speed** with minimal impact on reconstruction quality.

---

## Column Descriptions

- **Example ID** — One of the three built-in Gradio demo videos.  
- **#Targets** — Number of humans selected for reconstruction.  
- **#Frames** — Total number of frames in the input video.  
- **Occl?** — Whether occlusion-aware refinement is enabled (**🟢 Yes / 🔴 No**) set: `completion.enable` in [`configs/body4d.yaml`](../../configs/body4d.yaml).  
- **Batch Size** — Number of frames processed simultaneously during 4D reconstruction. set: `sam_3d_body.batch_size` in [`configs/body4d.yaml`](../../configs/body4d.yaml). 
- **Masklets Peak** — Peak GPU memory during masklet generation (SAM-3 propagation).  
- **Masklets Time** — Runtime for masklet generation.  
- **4D Peak** — Peak GPU memory during 4D human mesh reconstruction.  
- **4D Time** — Runtime for 4D reconstruction.  

---

## Profiling Results (H800 80GB VRAM, 120GB RAM)

| Example ID | #Targets | #Frames | Occl? | Batch Size | Masklets Peak | Masklets Time | 4D Peak | 4D Time |
|-----------:|---------:|--------:|:-----:|-----------:|----------------:|----------------:|----------:|----------:|
| EX-01      | 1        | 100     | 🔴     | 64          | 9.90 GB        | 15.55 s        | 14.49 GB | 1m 10.3s |
| EX-02      | 5        | 90      | 🔴     | 64          | 10.52 GB       | 30.92 s        | 40.87 GB | 2m 55.1s |
| EX-02      | 5        | 90      | 🟢     | 64          | 11.77 GB       | 30.75 s        | 53.28 GB        | 26m 6.6s        |
| EX-02      | 5        | 90      | 🟢     | 32          | 11.77 GB       | 31.01 s        | 35.19 GB        | 27m 15.7s        |
| EX-03      | 6        | 64      | 🔴     | 64          | 11.29 GB       | 24.48 s        | 46.75 GB | 2m 36.9s |
| EX-03      | 6        | 64      | 🟢     | 64          | 11.57 GB       | 24.87 s        | 52.91 GB | 27m 25.6s |
| EX-03      | 6        | 64      | 🟢     | 32          | 11.57 GB       | 25.19 s        | 34.79 GB | 27m 8.5s |
