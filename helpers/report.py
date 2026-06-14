#!/usr/bin/env python3
"""
Generate the quality report PDF — WebODM's "Qualitätsbericht"
(``odm_report/report.pdf``).

Gathers stats from the workdir (images / cameras / dense points / mesh /
orthophoto / CRS) and renders a compact PDF with a stats table and, when an
orthophoto exists, a thumbnail. Self-contained: reportlab for the PDF, GDAL only
for the optional thumbnail (both already in the image). Non-fatal upstream — a
missing report must not fail the task.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import georef_bridge as gb  # noqa: E402


def _ply_vertex_count(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        for _ in range(40):
            line = f.readline().decode("latin-1", "ignore").strip()
            if line.startswith("element vertex"):
                return int(line.split()[-1])
            if line == "end_header":
                break
    return None


def _obj_counts(path):
    v = fcount = 0
    if path and os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            for line in f:
                if line.startswith("v "):
                    v += 1
                elif line.startswith("f "):
                    fcount += 1
    return v, fcount


def _ortho_stats(tif):
    """(width, height, gsd_cm, area_m2, coverage_pct, thumb_png_path) or None."""
    if not os.path.exists(tif):
        return None
    try:
        from osgeo import gdal
    except ImportError:
        return None
    ds = gdal.Open(tif)
    if ds is None:
        return None
    W, H = ds.RasterXSize, ds.RasterYSize
    gt = ds.GetGeoTransform()
    gsd = abs(gt[1])
    bands = ds.RasterCount
    cov = 100.0
    if bands >= 4:
        a = ds.GetRasterBand(4).ReadAsArray()
        cov = 100.0 * (a > 0).mean()
        area = float((a > 0).sum()) * gsd * gsd
    else:
        area = W * H * gsd * gsd
    # thumbnail (max 480 px wide) via gdal -> PNG
    thumb = None
    try:
        from PIL import Image
        import numpy as np
        scale = max(1, W // 480)
        tw, th = W // scale, H // scale
        chans = [ds.GetRasterBand(b + 1).ReadAsArray(buf_xsize=tw, buf_ysize=th)
                 for b in range(min(bands, 4))]
        arr = np.dstack(chans).astype(np.uint8)
        thumb = tif + ".thumb.png"
        Image.fromarray(arr[:, :, :3] if arr.shape[2] >= 3 else arr[:, :, 0]).save(thumb)
    except Exception:
        thumb = None
    return W, H, gsd * 100.0, area, cov, thumb


def _dsm_stats(tif):
    """(width, height, gsd_cm, zmin, zmax) for the DSM, or None."""
    if not os.path.exists(tif):
        return None
    try:
        from osgeo import gdal
    except ImportError:
        return None
    ds = gdal.Open(tif)
    if ds is None:
        return None
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    import numpy as np
    arr = band.ReadAsArray()
    valid = arr[arr != nodata] if nodata is not None else arr.ravel()
    if valid.size == 0:
        return None
    gt = ds.GetGeoTransform()
    return ds.RasterXSize, ds.RasterYSize, abs(gt[1]) * 100.0, float(valid.min()), float(valid.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--sparse-engine", default="")
    ap.add_argument("--matcher", default="")
    ap.add_argument("--mapper", default="")
    ap.add_argument("--refine-iters", default="")
    args = ap.parse_args()
    W = args.work

    model_dir = gb._find_colmap_model(W)
    n_images = len(gb._read_images_full(model_dir)) if model_dir else None
    n_cams = len(gb._read_cameras(model_dir)) if model_dir else None
    dense = _ply_vertex_count(os.path.join(W, "scene_dense.ply"))
    from openmvs_mesh import find_mesh_obj
    obj = find_mesh_obj(W)
    mv, mf = _obj_counts(os.path.join(W, obj) if obj else None)

    tr = {}
    trp = os.path.join(W, "georef_transform.json")
    if os.path.exists(trp):
        tr = json.load(open(trp))
    ortho = _ortho_stats(os.path.join(W, "odm_orthophoto.tif"))
    dsm = _dsm_stats(os.path.join(W, "odm_dem", "dsm.tif"))
    dtm = _dsm_stats(os.path.join(W, "odm_dem", "dtm.tif"))   # generic single-band DEM reader

    rows = [["Dataset", args.name or os.path.basename(os.path.dirname(W)) or W]]
    if n_images is not None:
        rows.append(["Images / cameras", f"{n_images} / {n_cams}"])
    if dense is not None:
        rows.append(["Dense points", f"{dense:,}"])
    if mf:
        rows.append(["Mesh", f"{mv:,} vertices, {mf:,} faces"])
    rows.append(["Georeferencing", f"{tr.get('source', 'n/a')} ({tr.get('crs', 'local')})"])
    res = tr.get("residuals")
    if res:
        src = "GCP" if "gcp" in str(tr.get("source", "")) else "camera/GPS"
        rows.append(["Georef RMS error",
                     f"{res['rms_3d']:.2f} m 3D ({res['rms_horizontal']:.2f} horiz, "
                     f"{res['rms_vertical']:.2f} vert; n={res['count']} {src})"])
    if ortho:
        Wpx, Hpx, gsd_cm, area, cov, _ = ortho
        rows.append(["Orthophoto", f"{Wpx} x {Hpx} px @ {gsd_cm:.1f} cm/px"])
        rows.append(["Area covered", f"{area:,.1f} m² ({cov:.0f}% of frame)"])
        # Orthophoto finishing (always written by orthophoto.py: residual tonal
        # variation + any radiometric balancing applied).
        fin_path = os.path.join(W, "odm_report", "orthophoto_finishing.json")
        if os.path.exists(fin_path):
            try:
                fin = json.load(open(fin_path))
            except Exception:
                fin = {}
            before = (fin.get("before") or {}).get("lowfreq_std")
            if before is not None:
                steps = fin.get("steps") or []
                applied = "+".join(steps) if steps else "none (diagnostic only)"
                after = (fin.get("after") or {}).get("lowfreq_std", before)
                rows.append(["Ortho finishing",
                             f"tonal variation {before:.1f} → {after:.1f} "
                             f"(8-bit luma std); balance: {applied}"])
    if dsm:
        Wd, Hd, gsd_cm, zmin, zmax = dsm
        rows.append(["DSM", f"{Wd} x {Hd} px @ {gsd_cm:.1f} cm/px, elev {zmin:.1f}..{zmax:.1f} m"])
    if dtm:
        Wd, Hd, gsd_cm, zmin, zmax = dtm
        rows.append(["DTM", f"{Wd} x {Hd} px @ {gsd_cm:.1f} cm/px, elev {zmin:.1f}..{zmax:.1f} m (bare earth)"])

    # Multi-epoch change detection (opt-in; only present with --align-to)
    chg_path = os.path.join(W, "odm_report", "change_detection.json")
    if os.path.exists(chg_path):
        try:
            chg = json.load(open(chg_path))
        except Exception:
            chg = {}
        cr = chg.get("coregistration") or {}
        after = cr.get("c2c_after") or cr.get("c2c_before") or {}
        if after.get("rms") is not None:
            rows.append(["Change: co-registration",
                         f"ICP residual {after['rms']*100:.1f} cm RMS "
                         f"(fitness {cr.get('fitness', 'n/a')})"])
        dod = chg.get("dod") or {}
        if "net_volume_m3" in dod:
            rows.append(["Change: DoD volume",
                         f"net {dod['net_volume_m3']:+.1f} m³ "
                         f"(fill {dod['volume_fill_m3']:.1f}, cut {dod['volume_cut_m3']:.1f}); "
                         f"changed area {dod['changed_area_m2']:.0f} m²"])
        m = chg.get("m3c2") or {}
        if m.get("available") and m.get("median_change_m") is not None:
            rows.append(["Change: M3C2",
                         f"median {m['median_change_m']:+.3f} m, "
                         f"{100*m['significant_fraction']:.0f}% significant "
                         f"(LoD {m['lod_median_m']*100:.1f} cm)"])
    proc = [p for p in [args.sparse_engine and f"sparse={args.sparse_engine}",
                        args.matcher and f"matcher={args.matcher}",
                        args.mapper and f"mapper={args.mapper}",
                        args.refine_iters and f"refine-iters={args.refine_iters}"] if p]
    if proc:
        rows.append(["Processing", ", ".join(proc)])

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, Image)
    except ImportError as e:
        print(f"[report] reportlab missing, cannot write report.pdf: {e}", file=sys.stderr)
        return

    st = getSampleStyleSheet()
    story = [Paragraph("Effigies — Quality Report", st["Title"]),
             Paragraph("COLMAP + full OpenMVS (Reconstruct/Refine/Texture) engine",
                       st["Normal"]),
             Spacer(1, 6 * mm)]
    tbl = Table(rows, colWidths=[55 * mm, 110 * mm])
    tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#444444")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    if ortho and ortho[5]:
        story += [Spacer(1, 6 * mm), Paragraph("Orthophoto", st["Heading2"]),
                  Image(ortho[5], width=165 * mm, height=165 * mm * ortho[1] / ortho[0])]

    out = os.path.join(W, "report.pdf")
    SimpleDocTemplate(out, pagesize=A4, title="Effigies Quality Report").build(story)
    if ortho and ortho[5] and os.path.exists(ortho[5]):
        os.remove(ortho[5])
    print(f"[report] wrote report.pdf ({len(rows)} stats"
          f"{', with orthophoto' if ortho and ortho[5] else ''})")


if __name__ == "__main__":
    main()
