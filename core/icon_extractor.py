"""Direct extraction of embedded Windows PE executable (.exe) icons on Linux."""

import os
import struct
from typing import Optional
from PyQt6.QtGui import QImage


def extract_exe_icon(exe_path: str, output_png: str) -> bool:
    """Extract the highest-resolution embedded icon from a Windows .exe file directly to PNG.
    
    Operates via file stream offsets so it runs with near-zero memory footprint even on
    multi-gigabyte game executables.
    """
    if not exe_path or not os.path.isfile(exe_path):
        return False

    try:
        with open(exe_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_len = f.tell()
            if file_len < 64:
                return False

            f.seek(0)
            if f.read(2) != b"MZ":
                return False

            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = struct.unpack("<I", pe_offset_bytes)[0]
            if pe_offset + 24 > file_len:
                return False

            f.seek(pe_offset)
            if f.read(4) != b"PE\0\0":
                return False

            coff_data = f.read(20)
            if len(coff_data) < 20:
                return False
            num_sections = struct.unpack_from("<H", coff_data, 2)[0]
            opt_hdr_size = struct.unpack_from("<H", coff_data, 16)[0]

            opt_hdr_data = f.read(opt_hdr_size)
            if len(opt_hdr_data) < opt_hdr_size or opt_hdr_size < 2:
                return False

            magic = struct.unpack_from("<H", opt_hdr_data, 0)[0]
            # Data Directory index 2: Resource Directory
            rsrc_dir_offset = (112 if magic == 0x20B else 96) + 16
            if rsrc_dir_offset + 8 > len(opt_hdr_data):
                return False

            rsrc_rva, rsrc_size = struct.unpack_from("<II", opt_hdr_data, rsrc_dir_offset)
            if rsrc_rva == 0 or rsrc_size == 0:
                return False

            f.seek(pe_offset + 24 + opt_hdr_size)
            sec_headers_data = f.read(num_sections * 40)
            sections = []
            for i in range(num_sections):
                s_off = i * 40
                if s_off + 24 <= len(sec_headers_data):
                    v_size, v_rva, raw_size, raw_ptr = struct.unpack_from("<IIII", sec_headers_data, s_off + 8)
                    sections.append((v_rva, v_size, raw_ptr, raw_size))

            def rva_to_offset(rva: int) -> Optional[int]:
                for v_rva, v_size, raw_ptr, raw_size in sections:
                    if v_rva <= rva < v_rva + max(v_size, raw_size):
                        return raw_ptr + (rva - v_rva)
                return None

            rsrc_base_offset = rva_to_offset(rsrc_rva)
            if not rsrc_base_offset:
                return False

            f.seek(rsrc_base_offset)
            rsrc_data = f.read(min(rsrc_size, 8 * 1024 * 1024))

            def parse_rsrc_dir(dir_offset: int):
                rel = dir_offset - rsrc_base_offset
                if rel < 0 or rel + 16 > len(rsrc_data):
                    return []
                num_named, num_id = struct.unpack_from("<HH", rsrc_data, rel + 12)
                total = num_named + num_id
                entries = []
                for i in range(total):
                    e_off = rel + 16 + i * 8
                    if e_off + 8 <= len(rsrc_data):
                        e_id, e_sub = struct.unpack_from("<II", rsrc_data, e_off)
                        entries.append((e_id, e_sub))
                return entries

            type_entries = parse_rsrc_dir(rsrc_base_offset)
            group_icon_entry = None
            icon_entries = {}

            for t_id, t_sub in type_entries:
                if t_id == 14 and (t_sub & 0x80000000):  # RT_GROUP_ICON
                    g_dir_off = rsrc_base_offset + (t_sub & 0x7FFFFFFF)
                    for g_id, g_sub in parse_rsrc_dir(g_dir_off):
                        if g_sub & 0x80000000:
                            lang_dir_off = rsrc_base_offset + (g_sub & 0x7FFFFFFF)
                            for l_id, l_sub in parse_rsrc_dir(lang_dir_off):
                                if not (l_sub & 0x80000000):
                                    data_entry_off = rsrc_base_offset + l_sub
                                    rel_de = data_entry_off - rsrc_base_offset
                                    if rel_de + 8 <= len(rsrc_data):
                                        data_rva, data_sz = struct.unpack_from("<II", rsrc_data, rel_de)
                                        file_off = rva_to_offset(data_rva)
                                        if file_off:
                                            group_icon_entry = (file_off, data_sz)
                                            break
                        if group_icon_entry:
                            break
                elif t_id == 3 and (t_sub & 0x80000000):  # RT_ICON
                    i_dir_off = rsrc_base_offset + (t_sub & 0x7FFFFFFF)
                    for i_id, i_sub in parse_rsrc_dir(i_dir_off):
                        if i_sub & 0x80000000:
                            lang_dir_off = rsrc_base_offset + (i_sub & 0x7FFFFFFF)
                            for l_id, l_sub in parse_rsrc_dir(lang_dir_off):
                                if not (l_sub & 0x80000000):
                                    data_entry_off = rsrc_base_offset + l_sub
                                    rel_de = data_entry_off - rsrc_base_offset
                                    if rel_de + 8 <= len(rsrc_data):
                                        data_rva, data_sz = struct.unpack_from("<II", rsrc_data, rel_de)
                                        file_off = rva_to_offset(data_rva)
                                        if file_off:
                                            icon_entries[i_id] = (file_off, data_sz)

            if not group_icon_entry or not icon_entries:
                return False

            g_off, g_sz = group_icon_entry
            f.seek(g_off)
            grp_data = f.read(g_sz)
            if len(grp_data) < 6:
                return False
            id_res, id_type, id_count = struct.unpack_from("<HHH", grp_data, 0)
            if id_type != 1 or id_count == 0:
                return False

            # Check if any icon resource entry is already a raw PNG (Vista+ high-res 256x256 PNG icon)
            best_png = None
            for i_id, (img_off, img_sz) in icon_entries.items():
                f.seek(img_off)
                head = f.read(8)
                if head.startswith(b"\x89PNG\r\n\x1a\n"):
                    f.seek(img_off)
                    png_bytes = f.read(img_sz)
                    if not best_png or len(png_bytes) > len(best_png):
                        best_png = png_bytes

            if best_png:
                img = QImage.fromData(best_png)
                if not img.isNull():
                    os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)
                    img.save(output_png, "PNG")
                    return True

            ico_entries = []
            current_offset = 6 + id_count * 16
            ico_images = []

            for idx in range(id_count):
                entry_off = 6 + idx * 14
                if entry_off + 14 > len(grp_data):
                    break
                w, h, c_count, reserved, planes, bit_count, bytes_in_res, icon_id = struct.unpack_from(
                    "<BBBBHHIH", grp_data, entry_off
                )
                if icon_id in icon_entries:
                    img_off, img_sz = icon_entries[icon_id]
                    f.seek(img_off)
                    raw_img = f.read(img_sz)
                    ico_entries.append(
                        struct.pack(
                            "<BBBBHHII",
                            w, h, c_count, reserved, planes, bit_count, len(raw_img), current_offset
                        )
                    )
                    current_offset += len(raw_img)
                    ico_images.append(raw_img)

            if not ico_images:
                return False

            ico_data = struct.pack("<HHH", 0, 1, len(ico_images)) + b"".join(ico_entries) + b"".join(ico_images)

            img = QImage.fromData(ico_data)
            if not img.isNull():
                os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)
                img.save(output_png, "PNG")
                return True

            return False
    except Exception:
        return False
