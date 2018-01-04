#############################################
# SCENE EXPORT - SHARED COMPONENTS
#############################################
import bpy
import bmesh
import struct
import mathutils
import math
from . import helpers, collision, prefs, material, autosplit
from bpy.props import *
from . prefs import *
from . autosplit import *
from . helpers import *
from . collision import *
from . material import *
from . constants import *
from . qb import *
from . level_manifest import *
from . export_thug1 import export_scn_sectors
from . export_thug2 import export_scn_sectors_ug2

class ExportError(Exception):
    pass


# METHODS
#############################################
def pack_pre(root_dir, files, output_file):
    pack = struct.pack
    with open(output_file, "wb") as outp:
        outp.write(pack("I", 0))
        outp.write(pack("I", 0xABCD0003))  # version
        outp.write(pack("I", len(files)))  # num files

        for file in files:
            adjusted_fn = bytes(os.path.relpath(file, root_dir), 'ascii') + b"\x00"
            if len(adjusted_fn) % 4 != 0:
                adjusted_fn = adjusted_fn + (b'\x00' * (4 - (len(adjusted_fn) % 4)))

            with open(file, "rb") as inp:
                data = inp.read()
            outp.write(pack("I", len(data)))  # data size
            outp.write(pack("I", 0))  # compressed data size
            outp.write(pack("I", len(adjusted_fn)))  # file name size
            outp.write(pack("I", crc_from_string(bytes(os.path.relpath(file, root_dir), 'ascii'))))  # file name checksum
            outp.write(adjusted_fn)  # file name
            outp.write(data)  # data

            offs = outp.tell()
            if offs % 4 != 0:
                outp.write(b'\x00' * (4 - (offs % 4)))

        total_bytes = outp.tell()
        outp.seek(0)
        outp.write(pack("I", total_bytes))

#----------------------------------------------------------------------------------
def do_export(operator, context, target_game):
    self = operator
    import subprocess, shutil, datetime

    addon_prefs = context.user_preferences.addons[ADDON_NAME].preferences
    base_files_dir_error = prefs._get_base_files_dir_error(addon_prefs)
    if base_files_dir_error:
        self.report({"ERROR"}, "Base files directory error: {} Check the base files directory addon preference. Aborting export.".format(base_files_dir_error))
        return {"CANCELLED"}
    base_files_dir = addon_prefs.base_files_dir

    if target_game == "THUG1":
        DEFAULT_SKY_SCN = self.skybox_name + ".scn.xbx"
        DEFAULT_SKY_TEX = self.skybox_name + ".tex.xbx"
    elif target_game == "THUG2":
        DEFAULT_SKY_SCN = self.skybox_name + ".scn.xbx"
        DEFAULT_SKY_TEX = self.skybox_name + ".tex.xbx"
    else:
        raise Exception("Unknown target game: {}".format(target_game))

    start_time = datetime.datetime.now()

    filename = self.filename
    directory = self.directory

    j = os.path.join

    def md(dir):
        if not os.path.exists(dir):
            os.makedirs(dir)

    ext_pre = (".prx" if target_game == "THUG2" else ".pre")
    ext_col = (".col" if (target_game == "THUG1" and not self.pack_pre) else ".col.xbx" )
    ext_scn = (".scn" if (target_game == "THUG1" and not self.pack_pre) else ".scn.xbx" )
    ext_tex = (".tex" if (target_game == "THUG1" and not self.pack_pre) else ".tex.xbx" )
    ext_qb = ".qb"

    self.report({'OPERATOR'}, "")
    self.report({'INFO'}, "-" * 20)
    self.report({'INFO'}, "Starting export of {} at {}".format(filename, start_time.time()))
    orig_objects, temporary_objects = [], []

    import sys
    logging_fh = logging.FileHandler(j(directory, filename + "_export.log"), mode='w')
    logging_fh.setFormatter(logging.Formatter("{asctime} [{levelname}]  {message}", style='{', datefmt="%H:%M:%S"))
    logging_ch = logging.StreamHandler(sys.stdout)
    logging_ch.setFormatter(logging.Formatter("{asctime} [{levelname}]  {message}", style='{', datefmt="%H:%M:%S"))
    global global_export_scale
    global_export_scale = operator.export_scale
    try:
        LOG.addHandler(logging_fh)
        LOG.addHandler(logging_ch)
        LOG.setLevel(logging.DEBUG)

        if self.generate_col_file or self.generate_scn_file or self.generate_scripts_files:
            orig_objects, temporary_objects = autosplit._prepare_autosplit_objects(operator, context,target_game)

        path = j(directory, "Levels\\" + filename)
        md(path)
        if self.generate_col_file:
            export_col(filename + ext_col, path, target_game, self)
            
        if self.generate_scn_file:
            self.report({'OPERATOR'}, "Generating scene file... ")
            export_scn(filename + ext_scn, path, target_game, self)

        if self.generate_tex_file:
            md(path)
            self.report({'OPERATOR'}, "Generating tex file... ")
            export_tex(filename + ext_tex, path, target_game, self)

        if self.generate_scn_file:
            skypath = j(directory, "Levels\\" + filename + "_sky")
            md(skypath)
            shutil.copy(
                j(base_files_dir, 'default_sky', DEFAULT_SKY_SCN),
                j(skypath, filename + "_sky" + ext_scn))
            shutil.copy(
                j(base_files_dir, 'default_sky', DEFAULT_SKY_TEX),
                j(skypath, filename + "_sky" + ext_tex))

        compilation_successful = None
        if self.generate_scripts_files:
            self.report({'OPERATOR'}, "Generating QB files... ")
            export_qb(filename, path, target_game, self)

            old_cwd = os.getcwd()
            os.chdir(path)
            compilation_successful = True

            import platform
            wine = [] if platform.system() == "Windows" else ["wine"]

            # #########################
            # Build NODEARRAY qb file
            try:
                print("Compiling {}.txt to QB...".format(filename))
                roq_output = subprocess.run(wine + [
                    j(base_files_dir, "roq.exe"),
                    "-c",
                    filename + ".txt"
                    ], stdout=subprocess.PIPE)
                if os.path.exists(filename + ".qb"):
                    os.remove(filename + ".qb")
                if os.path.exists(filename + ".txt.qb"):
                    os.rename(filename + ".txt.qb", filename + ".qb")
                else:
                    self.report({"ERROR"}, "{}\n\nCompiler output:\nFailed to compile the QB file.".format(
                        '\n'.join(reversed(roq_output.stdout.decode().split("\r\n")))))
                    compilation_successful = False

            finally:
                os.chdir(old_cwd)
            # /Build NODEARRAY qb file
            # #########################
            
            # #########################
            # Build _SCRIPTS qb file
            if os.path.exists(path + "/" + filename + "_scripts.txt"):
                print("Compiling {}_scripts.txt to QB...".format(filename))
                os.chdir(path)
                try:
                    roq_output = subprocess.run(wine + [
                        j(base_files_dir, "roq.exe"),
                        "-c",
                        filename + "_scripts.txt"
                        ], stdout=subprocess.PIPE)
                    if os.path.exists(filename + "_scripts.qb"):
                        os.remove(filename + "_scripts.qb")
                    if os.path.exists(filename + "_scripts.txt.qb"):
                        os.rename(filename + "_scripts.txt.qb", filename + "_scripts.qb")
                    else:
                        self.report({"ERROR"}, "{}\n\nCompiler output:\nFailed to compile the QB file.".format(
                            '\n'.join(reversed(roq_output.stdout.decode().split("\r\n")))))
                        compilation_successful = False

                finally:
                    os.chdir(old_cwd)
            # /Build _SCRIPTS qb file
            # #########################
            

        # #########################
        # Build PRE files
        if self.pack_pre:
            md(j(directory, "pre"))
            if self.generate_scripts_files:
                pack_files = []
                pack_files.append(j(path, filename + ext_qb))
                pack_files.append(j(path, filename + "_scripts" + ext_qb))
                if target_game == "THUG2":
                    pack_files.append(j(path, filename + "_thugpro" + ext_qb))
                    pack_pre( directory, pack_files, j(directory, "pre", filename + "_scripts" + ext_pre) )
                else:
                    pack_pre( directory, pack_files, j(directory, "pre", filename + ext_pre) )
                self.report({'OPERATOR'}, "Exported " + j(directory, "pre", filename + ext_pre))
                
            if self.generate_col_file:
                pack_files = []
                pack_files.append(j(path, filename + ext_col))
                pack_pre( directory, pack_files, j(directory, "pre", filename + "col" + ext_pre) )
                self.report({'OPERATOR'}, "Exported " + j(directory, "pre", filename + "col" + ext_pre))
            if self.generate_scn_file:
                pack_files = []
                pack_files.append(j(path, filename + ext_scn))
                pack_files.append(j(path, filename + ext_tex))
                pack_files.append(j(skypath, filename + "_sky" + ext_scn))
                pack_files.append(j(skypath, filename + "_sky" + ext_tex))
                pack_pre( directory, pack_files, j(directory, "pre", filename + "scn" + ext_pre) )
                self.report({'OPERATOR'}, "Exported " + j(directory, "pre", filename + "scn" + ext_pre))
                
        # /Build PRE files
        # #########################
                         
        end_time = datetime.datetime.now()
        if (compilation_successful is None) or compilation_successful:
            print("EXPORT COMPLETE! Thank you for waiting :)")
            self.report({'INFO'}, "Exported level {} at {} (time taken: {})".format(filename, end_time.time(), end_time - start_time))
        else:
            print("EXPORT FAILED! Uh oh :(")
            self.report({'WARNING'}, "Failed exporting level {} at {} (time taken: {})".format(filename, end_time.time(), end_time - start_time))
            
        # -------------------------------------------------
        # Final step: generate level manifest .json file!
        # -------------------------------------------------
        export_level_manifest_json(filename, path, self, context.scene.thug_level_props)
        
    except ExportError as e:
        self.report({'ERROR'}, "Export failed.\nExport error: {}".format(str(e)))
    except Exception as e:
        LOG.debug(e)
        raise
    finally:
        global_export_scale = 1
        LOG.removeHandler(logging_fh)
        LOG.removeHandler(logging_ch)
        autosplit._cleanup_autosplit_objects(operator, context, target_game, orig_objects, temporary_objects)
    return {'FINISHED'}

#----------------------------------------------------------------------------------
def do_export_model(operator, context, target_game):
    self = operator
    import subprocess, shutil, datetime

    addon_prefs = context.user_preferences.addons[ADDON_NAME].preferences
    base_files_dir_error = prefs._get_base_files_dir_error(addon_prefs)
    if base_files_dir_error:
        self.report({"ERROR"}, "Base files directory error: {} Check the base files directory addon preference. Aborting export.".format(base_files_dir_error))
        return {"CANCELLED"}
    base_files_dir = addon_prefs.base_files_dir

    if not target_game == "THUG1" and not target_game == "THUG2":
        raise Exception("Unknown target game: {}".format(target_game))

    start_time = datetime.datetime.now()

    filename = self.filename
    directory = self.directory

    j = os.path.join

    def md(dir):
        if not os.path.exists(dir):
            os.makedirs(dir)

    ext_col = (".col" if target_game == "THUG1" else ".col.xbx" )
    ext_scn = (".mdl" if target_game == "THUG1" else ".mdl.xbx" )
    ext_tex = (".tex" if target_game == "THUG1" else ".tex.xbx" )
    ext_qb = ".qb"
    if self.model_type == "skin":
        ext_scn = (".skin" if target_game == "THUG1" else ".skin.xbx" )
    
    self.report({'OPERATOR'}, "")
    self.report({'INFO'}, "-" * 20)
    self.report({'INFO'}, "Starting export of {} at {}".format(filename, start_time.time()))
    orig_objects, temporary_objects = [], []

    import sys
    logging_fh = logging.FileHandler(j(directory, filename + "_export.log"), mode='w')
    logging_fh.setFormatter(logging.Formatter("{asctime} [{levelname}]  {message}", style='{', datefmt="%H:%M:%S"))
    logging_ch = logging.StreamHandler(sys.stdout)
    logging_ch.setFormatter(logging.Formatter("{asctime} [{levelname}]  {message}", style='{', datefmt="%H:%M:%S"))
    global global_export_scale
    global_export_scale = operator.export_scale
    try:
        LOG.addHandler(logging_fh)
        LOG.addHandler(logging_ch)
        LOG.setLevel(logging.DEBUG)

        orig_objects, temporary_objects = autosplit._prepare_autosplit_objects(operator, context,target_game)

        path = j(directory, "Models/" + filename)
        md(path)
        
        # Generate COL file
        self.report({'OPERATOR'}, "Generating collision file... ")
        export_col(filename + ext_col, path, target_game, self)
        
        # Generate SCN/MDL file
        self.report({'OPERATOR'}, "Generating scene file... ")
        export_scn(filename + ext_scn, path, target_game, self)

        # Generate TEX file
        self.report({'OPERATOR'}, "Generating tex file... ")
        export_tex(filename + ext_tex, path, target_game, self)
            
        # Maybe generate QB file
        compilation_successful = None
        if self.generate_scripts_files:
            self.report({'OPERATOR'}, "Generating QB files... ")
            export_model_qb(filename, path, target_game, self)

            old_cwd = os.getcwd()
            os.chdir(path)
            compilation_successful = True

            import platform
            wine = [] if platform.system() == "Windows" else ["wine"]

            try:
                roq_output = subprocess.run(wine + [
                    j(base_files_dir, "roq.exe"),
                    "-c",
                    filename + ".txt"
                    ], stdout=subprocess.PIPE)
                if os.path.exists(filename + ".qb"):
                    os.remove(filename + ".qb")
                if os.path.exists(filename + ".txt.qb"):
                    os.rename(filename + ".txt.qb", filename + ".qb")
                else:
                    self.report({"ERROR"}, "{}\n\nCompiler output:\nFailed to compile the QB file.".format(
                        '\n'.join(reversed(roq_output.stdout.decode().split("\r\n")))))
                    compilation_successful = False

            finally:
                os.chdir(old_cwd)

        end_time = datetime.datetime.now()
        if (compilation_successful is None) or compilation_successful:
            self.report({'INFO'}, "Exported model {} at {} (time taken: {})".format(filename, end_time.time(), end_time - start_time))
        else:
            self.report({'WARNING'}, "Failed exporting model {} at {} (time taken: {})".format(filename, end_time.time(), end_time - start_time))
            
    except ExportError as e:
        self.report({'ERROR'}, "Export failed.\nExport error: {}".format(str(e)))
    except Exception as e:
        LOG.debug(e)
        raise
    finally:
        global_export_scale = 1
        LOG.removeHandler(logging_fh)
        LOG.removeHandler(logging_ch)
        autosplit._cleanup_autosplit_objects(operator, context, target_game, orig_objects, temporary_objects)
    return {'FINISHED'}


#----------------------------------------------------------------------------------
def export_scn(filename, directory, target_game, operator=None):
    def w(fmt, *args):
        outp.write(struct.pack(fmt, *args))

    output_file = os.path.join(directory, filename)
    with open(output_file, "wb") as outp:
        w("3I", 1, 1, 1)

        export_materials(outp, target_game, operator)
        if target_game == "THUG2":
            export_scn_sectors_ug2(outp, operator)
        elif target_game == "THUG1":
            export_scn_sectors(outp, operator)
        else:
            raise Exception("Unknown target game: {}".format(target_game))

        w("i", 0)  # number of hierarchy objects

#----------------------------------------------------------------------------------
def export_col(filename, directory, target_game, operator=None):
    from io import BytesIO
    p = Printer()
    output_file = os.path.join(directory, filename)

    bm = bmesh.new()
    # Applies modifiers and triangulates mesh - unless the 'speed hack' export option is on
    def triang(o):
        if operator.speed_hack:
            final_mesh = o.data
            bm.clear()
            bm.from_mesh(final_mesh)
        else:
            final_mesh = o.to_mesh(bpy.context.scene, True, 'PREVIEW')
            if helpers._need_to_flip_normals(o):
                temporary_object = helpers._make_temp_obj(final_mesh)
                try:
                    bpy.context.scene.objects.link(temporary_object)
                    # temporary_object.matrix_world = o.matrix_world
                    helpers._flip_normals(temporary_object)
                finally:
                    if bpy.context.mode != "OBJECT":
                        bpy.ops.object.mode_set(mode="OBJECT")
                    bpy.context.scene.objects.unlink(temporary_object)
                    bpy.data.objects.remove(temporary_object)
            
            bm.clear()
            bm.from_mesh(final_mesh)
            bmesh.ops.triangulate(bm, faces=bm.faces)
            bm.faces.ensure_lookup_table()
            bm.faces.index_update()
            bpy.data.meshes.remove(final_mesh)
        return

    out_objects = [o for o in bpy.data.objects
                   if (o.type == "MESH"
                    and getattr(o, 'thug_export_collision', True)
                    and not o.get("thug_autosplit_object_no_export_hack", False))]
    total_verts = 0 # sum(len(bm.verts) for o in out_objects if [triang(o)])
    total_faces = 0 # sum(len(bm.faces) for o in out_objects if [triang(o)])

    with open(output_file, "wb") as outp:
        def w(fmt, *args):
            outp.write(struct.pack(fmt, *args))

        verts_out = BytesIO()
        intensities_out = BytesIO()
        faces_out = BytesIO()
        thug2_thing_out = BytesIO()
        nodes_out = BytesIO()

        w("i", 10 if target_game == "THUG2" else 9) # version
        w("i", len(out_objects)) # num objects
        total_verts_offset = outp.tell()
        w("i", total_verts)
        w("i", total_faces) # large faces
        w("i", 0) # small faces
        w("i", total_verts) # large verts
        w("i", 0) # small verts
        w("i", 0) # padding

        obj_face_offset = 0
        obj_vert_offset = 0
        obj_bsp_offset = 0
        obj_intensity_offset = 0

        bsp_nodes_size = 0
        node_face_index_offset = 0
        node_faces = []

        DBG = lambda *args: LOG.debug(" ".join(str(arg) for arg in args))

        for o in out_objects:
            def w(fmt, *args):
                outp.write(struct.pack(fmt, *args))

            LOG.debug("Exporting object: {}".format(o.name))
            triang(o)
            total_verts += len(bm.verts)
            total_faces += len(bm.faces)

            if "thug_checksum" in o:
                w("i", o["thug_checksum"])
            else:
                
                clean_name = get_clean_name(o)
                if is_hex_string(clean_name):
                    w("I", int(clean_name, 0))  # checksum
                else:
                    w("I", crc_from_string(bytes(clean_name, 'ascii')))  # checksum
                    
            w("H", o.thug_col_obj_flags)
            if len(bm.verts) > 2**16:
                raise ExportError("Too many vertices in an object: {} (has {}, max is {}). Consider using Autosplit.".format(o.name, len(bm.verts), 2**16))
            w("H", len(bm.verts))
            MAX_TRIS = 6000 # min(6000, 2**16)
            #if (len(bm.faces) * (3 if target_game == "THUG2" else 1)) > MAX_TRIS:
            if len(bm.faces) > MAX_TRIS:
                raise ExportError("Too many tris in an object: {} (has {}, max is {}). Consider using Autosplit.".format(
                    o.name,
                    len(bm.faces),
                    MAX_TRIS))
                    # 2**16 // (3 if target_game == "THUG2" else 1)))
            w("H", len(bm.faces))
            w("?", False) # use face small
            w("?", False) # use fixed verts
            w("I", obj_face_offset)
            obj_face_offset += SIZEOF_LARGE_FACE * len(bm.faces)
            #obj_matrix = get_scale_matrix(o) if o.thug_object_class == "LevelObject" else o.matrix_world
            obj_matrix = o.matrix_world
            if operator.is_park_editor: 
                # AFAIK we don't modify the bounding box for dictionary collision, only the scene.
                # But if this changes I'll update it here!
                bbox = get_bbox2(bm.verts, obj_matrix)
            else:
                bbox = get_bbox2(bm.verts, obj_matrix)
            w("4f", *bbox[0])
            w("4f", *bbox[1])
            w("I", obj_vert_offset)
            obj_vert_offset += SIZEOF_FLOAT_VERT * len(bm.verts)
            w("I", obj_bsp_offset)
            obj_bsp_tree = make_bsp_tree(o, bm.faces[:])
            obj_bsp_offset += len(list(iter_tree(obj_bsp_tree))) * SIZEOF_BSP_NODE
            w("I", obj_intensity_offset)
            obj_intensity_offset += len(bm.verts)
            w("I", 0) # padding

            def w(fmt, *args):
                verts_out.write(struct.pack(fmt, *args))

            for v in bm.verts:
                w("3f", *to_thug_coords(obj_matrix * v.co))

            def w(fmt, *args):
                intensities_out.write(struct.pack(fmt, *args))

            intensities_out.write(b'\xff' * len(bm.verts))

            def w(fmt, *args):
                faces_out.write(struct.pack(fmt, *args))

            cfl = bm.faces.layers.int.get("collision_flags")
            ttl = bm.faces.layers.int.get("terrain_type")

            # bm.verts.ensure_lookup_table()
            # Face flags are output here!
            for face in bm.faces:
                if cfl and (face[cfl] & FACE_FLAGS["mFD_TRIGGER"]):
                    if o.thug_triggerscript_props.triggerscript_type == "None" or \
                    (o.thug_triggerscript_props.triggerscript_type == "Custom" and o.thug_triggerscript_props.custom_name == ""):
                        # This object has a Trigger face, but no TriggerScript assigned
                        # Normally this would crash the game, so let's create and assign a blank script!
                        get_triggerscript("io_thps_scene_NullScript")
                        o.thug_triggerscript_props.triggerscript_type = "Custom"
                        o.thug_triggerscript_props.custom_name = "script_io_thps_scene_NullScript"
                        LOG.debug("WARNING: Object {} has trigger faces but no TriggerScript. A blank script was assigned.".format(o.name))
                        #raise Exception("Collision object " + o.name + " has a trigger face with no TriggerScript attached to the object! This is for your own safety!")
                        
                w("H", face[cfl] if cfl else 0)
                tt = collision._resolve_face_terrain_type(o, bm, face)
                w("H", tt)
                for vert in face.verts:
                    w("H", vert.index)

            if target_game == "THUG2":
                def w(fmt, *args):
                    thug2_thing_out.write(struct.pack(fmt, *args))

                thug2_thing_out.write(b'\x00' * len(bm.faces))

            def w(fmt, *args):
                nodes_out.write(struct.pack(fmt, *args))

            bsp_nodes_start = bsp_nodes_size
            node_list, node_indices = tree_to_list(obj_bsp_tree)
            for idx, node in enumerate(node_list):
                # assert idx == node_indices[id(node)]
                # DBG(node_indices[id(node)])
                bsp_nodes_size += SIZEOF_BSP_NODE
                if isinstance(node, BSPLeaf):
                    w("B", 3)  # the axis it is split on (0 = X, 1 = Y, 2 = Z, 3 = Leaf)
                    w("B", 0)  # padding
                    w("H", len(node.faces) * (3 if False and target_game == "THUG2" else 1))
                    w("I", node_face_index_offset)
                    # exported |= set(node.faces)
                    for face in node.faces:
                        # assert bm.faces[face.index] == face
                        node_faces.append(face.index)
                    node_face_index_offset += len(node.faces) * (3 if False and target_game == "THUG2" else 1)
                else:
                    split_axis_and_point = (
                        (node.split_axis & 0x3) |
                        # 1 |
                        (int(node.split_point * 16.0) << 2)
                        )
                    w("i", split_axis_and_point)
                    w("I", (bsp_nodes_start + node_indices[id(node.left)] * SIZEOF_BSP_NODE))

        def w(fmt, *args):
            outp.write(struct.pack(fmt, *args))

        tmp_offset = outp.tell()
        outp.seek(total_verts_offset)
        w("i", total_verts)
        w("i", total_faces)
        w("i", 0) # small faces
        w("i", total_verts)
        outp.seek(tmp_offset)

        LOG.debug("offset obj list: {}".format(outp.tell()))
        outp.write(b'\x00' * calc_alignment_diff(outp.tell(), 16))

        LOG.debug("offset verts: {}".format(outp.tell()))
        outp.write(verts_out.getbuffer())

        LOG.debug("offset intensities: {}".format(outp.tell()))
        # intensity
        outp.write(intensities_out.getbuffer())

        alignment_diff = calc_alignment_diff(outp.tell(), 4)
        if alignment_diff != 0:
            LOG.debug("A: ".format(alignment_diff))
        outp.write(b'\x00' * alignment_diff)
        # outp.write(b'\x00' * calc_alignment_diff(SIZEOF_FLOAT_VERT * total_verts + total_verts), 4)

        LOG.debug("offset faces: {}".format(outp.tell()))
        outp.write(faces_out.getbuffer())

        if target_game == "THUG2":
            # alignment_diff = calc_alignment_diff(total_verts, 4)
            alignment_diff = calc_alignment_diff(outp.tell(), 2)
            if alignment_diff != 0:
                LOG.debug("B: {}".format(alignment_diff))
            outp.write(b'\x00' * alignment_diff)
        else:
            # LOG.debug("B TH1!")
            if total_faces & 1:
                outp.write(b'\x00' * 2)

        if target_game == "THUG2":
            LOG.debug("offset thug2 thing: {}".format(outp.tell()))
            outp.write(thug2_thing_out.getbuffer())

            alignment_diff = calc_alignment_diff(outp.tell(), 4)
            if alignment_diff != 0:
                LOG.debug("C: {}".format(alignment_diff))
            outp.write(b'\x00' * alignment_diff)

        LOG.debug("offset nodes: {}".format(outp.tell()))

        w("I", bsp_nodes_size)
        outp.write(nodes_out.getbuffer())

        for face in node_faces:
            w("H", face)

    bm.free()



#----------------------------------------------------------------------------------
def calc_alignment_diff(offset, alignment):
    assert offset >= 0 and alignment >= 0
    if offset % alignment == 0:
        return 0
    return alignment - (offset % alignment)



# OPERATORS
#############################################
class SceneToTHUGFiles(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug_xbx"
    bl_label = "Scene to THUG1 level files"
    # bl_options = {'REGISTER', 'UNDO'}

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=False)
    use_vc_hack = BoolProperty(name="Vertex color hack",
        description = "Doubles intensity of vertex colours. Enable if working with an imported scene that appears too dark in game."
        , default=False)
    speed_hack = BoolProperty(name="No modifiers (speed hack)",
        description = "Don't apply any modifiers to objects. Much faster with large scenes, but all mesh must be triangles prior to export.", default=False)
    # AUTOSPLIT SETTINGS
    autosplit_everything = BoolProperty(name="Autosplit All",
        description = "Applies the autosplit setting to all objects in the scene, with default settings.", default=False)
    autosplit_faces_per_subobject = IntProperty(name="Faces Per Subobject",
        description="The max amount of faces for every created subobject.",
        default=800, min=50, max=6000)
    autosplit_max_radius = FloatProperty(name="Max Radius",
        description="The max radius of for every created subobject.",
        default=2000, min=100, max=5000)
    # /AUTOSPLIT SETTINGS
    pack_pre = BoolProperty(name="Pack files into .prx", default=True)
    is_park_editor = BoolProperty(name="Is Park Editor",
        description="Use this option when exporting a park editor dictionary.", default=False)
    generate_tex_file = BoolProperty(name="Generate a .tex file", default=True)
    generate_scn_file = BoolProperty(name="Generate a .scn file", default=True)
    generate_col_file = BoolProperty(name="Generate a .col file", default=True)
    generate_scripts_files = BoolProperty(name="Generate scripts", default=True)

#    filepath = StringProperty()
    skybox_name = StringProperty(name="Skybox name", default="THUG_Sky")
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(
        name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)
    only_offset_lightmap = BoolProperty(name="Only Lightmaps", default=False, description="Mipmap offset only applies to lightmap textures.")

    def execute(self, context):
        return do_export(self, context, "THUG1")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}
        
    def draw(self, context):
        self.layout.row().prop(self, "skybox_name")
        self.layout.row().prop(self, "generate_vertex_color_shading")
        self.layout.row().prop(self, "use_vc_hack")
        self.layout.row().prop(self, "speed_hack")
        self.layout.row().prop(self, "autosplit_everything")
        if self.autosplit_everything:
            box = self.layout.box().column(True)
            box.row().prop(self, "autosplit_faces_per_subobject")
            box.row().prop(self, "autosplit_max_radius")
        self.layout.row().prop(self, "pack_pre")
        self.layout.row().prop(self, "generate_tex_file")
        self.layout.row().prop(self, "generate_scn_file")
        self.layout.row().prop(self, "generate_col_file")
        self.layout.row().prop(self, "generate_scripts_files")
        self.layout.row().prop(self, "export_scale")
        box = self.layout.box().column(True)
        box.row().prop(self, "mipmap_offset")
        box.row().prop(self, "only_offset_lightmap")
        
#----------------------------------------------------------------------------------
class SceneToTHUGModel(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug_model"
    bl_label = "Scene to THUG1 model"
    # bl_options = {'REGISTER', 'UNDO'}

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=False)
    is_park_editor = BoolProperty(name="Is Park Editor", default=False, options={'HIDDEN'})
    use_vc_hack = BoolProperty(name="Vertex color hack", description = "Doubles intensity of vertex colours.", default=False)
    speed_hack = BoolProperty(name="No modifiers (speed hack)",
        description = "Don't apply any modifiers to objects. Much faster with large scenes, but all mesh must be triangles prior to export.", default=False)
    
    # AUTOSPLIT SETTINGS
    autosplit_everything = BoolProperty(name="Autosplit All"
        , description = "Applies the autosplit setting to all objects in the scene, with default settings."
        , default=False)
    autosplit_faces_per_subobject = IntProperty(name="Faces Per Subobject",
        description="The max amount of faces for every created subobject.",
        default=800, min=50, max=6000)
    autosplit_max_radius = FloatProperty(name="Max Radius",
        description="The max radius of for every created subobject.",
        default=2000, min=100, max=5000)
    # /AUTOSPLIT SETTINGS
    is_park_editor = BoolProperty(name="Is Park Editor", default=False, options={'HIDDEN'})
    model_type = EnumProperty(items = (
        ("skin", ".skin", "Character skin, used for playable characters and pedestrians."),
        ("mdl", ".mdl", "Model used for vehicles and other static mesh."),
    ), name="Model Type", default="skin")
    generate_scripts_files = BoolProperty(
        name="Generate scripts",
        default=True)
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(
        name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)
    only_offset_lightmap = BoolProperty(name="Only Lightmaps", default=False, description="Mipmap offset only applies to lightmap textures.")
        
    def execute(self, context):
        return do_export_model(self, context, "THUG1")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}
        
    def draw(self, context):
        self.layout.row().prop(self, "generate_vertex_color_shading")
        self.layout.row().prop(self, "use_vc_hack")
        self.layout.row().prop(self, "speed_hack")
        self.layout.row().prop(self, "autosplit_everything")
        if self.autosplit_everything:
            box = self.layout.box().column(True)
            box.row().prop(self, "autosplit_faces_per_subobject")
            box.row().prop(self, "autosplit_max_radius")
        self.layout.row().prop(self, "model_type", expand=True)
        self.layout.row().prop(self, "generate_scripts_files")
        self.layout.row().prop(self, "export_scale")
        box = self.layout.box().column(True)
        box.row().prop(self, "mipmap_offset")
        box.row().prop(self, "only_offset_lightmap")

# OPERATORS
#############################################
class SceneToTHUG2Files(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug2_xbx"
    bl_label = "Scene to THUG2/PRO level files"

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=False)
    use_vc_hack = BoolProperty(name="Vertex color hack",default=False, options={'HIDDEN'})
    speed_hack = BoolProperty(name="No modifiers (speed hack)",
        description = "Don't apply any modifiers to objects. Much faster with large scenes, but all mesh must be triangles prior to export.", default=False)
    # AUTOSPLIT SETTINGS
    autosplit_everything = BoolProperty(name="Autosplit All"
        , description = "Applies the autosplit setting to all objects in the scene, with default settings."
        , default=False)
    autosplit_faces_per_subobject = IntProperty(name="Faces Per Subobject",
        description="The max amount of faces for every created subobject.",
        default=800, min=50, max=6000)
    autosplit_max_radius = FloatProperty(name="Max Radius",
        description="The max radius of for every created subobject.",
        default=2000, min=100, max=5000)
    # /AUTOSPLIT SETTINGS
    is_park_editor = BoolProperty(name="Is Park Editor",
        description="Use this option when exporting a park editor dictionary.", default=False)
    pack_pre = BoolProperty(name="Pack files into .prx", default=True)
    generate_tex_file = BoolProperty(
        name="Generate a .tex file",
        description="If you have already generated a .tex file, and didn't change/add any new images in meantime, you can uncheck this.", default=True)
    generate_scn_file = BoolProperty(name="Generate a .scn file", default=True)
    generate_col_file = BoolProperty(name="Generate a .col file", default=True)
    generate_scripts_files = BoolProperty(name="Generate scripts", default=True)

    skybox_name = StringProperty(name="Skybox name", default="THUG2_Sky")
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)
    only_offset_lightmap = BoolProperty(name="Only Lightmaps", default=False, description="Mipmap offset only applies to lightmap textures.")

    def execute(self, context):
        return do_export(self, context, "THUG2")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.row().prop(self, "skybox_name")
        self.layout.row().prop(self, "generate_vertex_color_shading")
        self.layout.row().prop(self, "use_vc_hack")
        self.layout.row().prop(self, "speed_hack")
        self.layout.row().prop(self, "autosplit_everything")
        if self.autosplit_everything:
            box = self.layout.box().column(True)
            box.row().prop(self, "autosplit_faces_per_subobject")
            box.row().prop(self, "autosplit_max_radius")
        self.layout.row().prop(self, "pack_pre")
        self.layout.row().prop(self, "generate_tex_file")
        self.layout.row().prop(self, "generate_scn_file")
        self.layout.row().prop(self, "generate_col_file")
        self.layout.row().prop(self, "generate_scripts_files")
        self.layout.row().prop(self, "export_scale")
        box = self.layout.box().column(True)
        box.row().prop(self, "mipmap_offset")
        box.row().prop(self, "only_offset_lightmap")

#----------------------------------------------------------------------------------
class SceneToTHUG2Model(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug2_model"
    bl_label = "Scene to THUG2 model"
    # bl_options = {'REGISTER', 'UNDO'}

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=False)
    use_vc_hack = BoolProperty(name="Vertex color hack",default=False, options={'HIDDEN'})
    speed_hack = BoolProperty(name="No modifiers (speed hack)",
        description = "Don't apply any modifiers to objects. Much faster with large scenes, but all mesh must be triangles prior to export.", default=False)
    # AUTOSPLIT SETTINGS
    autosplit_everything = BoolProperty(name="Autosplit All",
        description = "Applies the autosplit setting to all objects in the scene, with default settings.", default=False)
    autosplit_faces_per_subobject = IntProperty(name="Faces Per Subobject",
        description="The max amount of faces for every created subobject.",
        default=800, min=50, max=6000)
    autosplit_max_radius = FloatProperty(name="Max Radius",
        description="The max radius of for every created subobject.",
        default=2000, min=100, max=5000)
    # /AUTOSPLIT SETTINGS
    is_park_editor = BoolProperty(name="Is Park Editor", default=False, options={'HIDDEN'})
    model_type = EnumProperty(items = (
        ("skin", ".skin", "Character skin, used for playable characters and pedestrians."),
        ("mdl", ".mdl", "Model used for vehicles and other static mesh."),
    ), name="Model Type", default="skin")
    generate_scripts_files = BoolProperty(name="Generate scripts", default=True)
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)
    only_offset_lightmap = BoolProperty(name="Only Lightmaps", default=False, description="Mipmap offset only applies to lightmap textures.")
        
    def execute(self, context):
        return do_export_model(self, context, "THUG2")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}
    
    def draw(self, context):
        self.layout.row().prop(self, "generate_vertex_color_shading")
        self.layout.row().prop(self, "use_vc_hack")
        self.layout.row().prop(self, "speed_hack")
        self.layout.row().prop(self, "autosplit_everything")
        if self.autosplit_everything:
            box = self.layout.box().column(True)
            box.row().prop(self, "autosplit_faces_per_subobject")
            box.row().prop(self, "autosplit_max_radius")
        self.layout.row().prop(self, "model_type")
        self.layout.row().prop(self, "generate_scripts_files")
        self.layout.row().prop(self, "export_scale")
        box = self.layout.box().column(True)
        box.row().prop(self, "mipmap_offset")
        box.row().prop(self, "only_offset_lightmap")
        