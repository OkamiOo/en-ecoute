--[[
  En Écoute pour OBS
  ---------------------------------------------------------------------------
  À ajouter une seule fois : Outils > Scripts > "+" > ce fichier.

  Le script démarre avec OBS le pont local (pont.py) qui reçoit le morceau
  joué dans le navigateur, et l'arrête quand OBS se ferme.
--]]

local obs = obslua

local SOURCE_NAME = "En écoute"
local PORT_DEFAUT = 4489

local cfg = {
  python = "",
  port = PORT_DEFAUT,
  autostart = true,
  cacher_pause = true,
  format = "{artiste} - {titre}",
  sortie = "",
  secours = false,
  maj = "demander",
  layout = "bar",
  theme = "dark",
  opacity = 100,
  align = "left",
  accent = "auto",
  label = "",
  show_art = true,
  show_progress = true,
  show_time = true,
  scale = 1.0,
  demo = false,
}

local started = false
local starting = false
local first_update = true
local settings_ref = nil
local bridge_signature = nil

-- ---------------------------------------------------------------------------
-- Appels Windows (LuaJIT FFI) : chemins en Unicode, lancement sans fenêtre
-- ---------------------------------------------------------------------------

local ffi_ok, ffi = pcall(require, "ffi")
local shell32 = nil
local CP_ACP, CP_UTF8 = 0, 65001
local SW_HIDE, SW_SHOWNORMAL = 0, 1

if ffi_ok then
  for _, decl in ipairs({
    "int __stdcall MultiByteToWideChar(unsigned int, unsigned long, const char*, int, uint16_t*, int);",
    "int __stdcall WideCharToMultiByte(unsigned int, unsigned long, const uint16_t*, int, char*, int, const char*, int*);",
    "unsigned long __stdcall GetFileAttributesW(const uint16_t*);",
    "unsigned long __stdcall GetEnvironmentVariableW(const uint16_t*, uint16_t*, unsigned long);",
    "void* __stdcall ShellExecuteW(void*, const uint16_t*, const uint16_t*, const uint16_t*, const uint16_t*, int);",
  }) do
    pcall(ffi.cdef, decl)
  end
  local ok, lib = pcall(ffi.load, "shell32")
  if ok then shell32 = lib end
end

local function wide(s, cp)
  local n = ffi.C.MultiByteToWideChar(cp or CP_UTF8, 0, s, -1, nil, 0)
  local buf = ffi.new("uint16_t[?]", n + 1)
  ffi.C.MultiByteToWideChar(cp or CP_UTF8, 0, s, -1, buf, n)
  return buf
end

local function narrow(w, cp)
  local n = ffi.C.WideCharToMultiByte(cp, 0, w, -1, nil, 0, nil, nil)
  if n <= 0 then return "" end
  local buf = ffi.new("char[?]", n)
  ffi.C.WideCharToMultiByte(cp, 0, w, -1, buf, n, nil, nil)
  return ffi.string(buf)
end

-- variable d'environnement en UTF-8 (os.getenv rend la page de code ANSI)
local function env(name)
  if not ffi_ok then return os.getenv(name) or "" end
  local buf = ffi.new("uint16_t[?]", 32768)
  local n = ffi.C.GetEnvironmentVariableW(wide(name), buf, 32768)
  if n == 0 or n >= 32768 then return "" end
  return narrow(buf, CP_UTF8)
end

-- io.open attend un chemin dans la page de code ANSI
local function open_file(path, mode)
  if ffi_ok then path = narrow(wide(path), CP_ACP) end
  return io.open(path, mode)
end

local function file_exists(path)
  if ffi_ok then
    return tonumber(ffi.C.GetFileAttributesW(wide(path))) ~= 4294967295
  end
  local fh = io.open(path, "r")
  if fh then fh:close() return true end
  return false
end

local SCRIPT_DIR = script_path():gsub("/", "\\")
local BRIDGE_PY = SCRIPT_DIR .. "pont.py"
local TEMP_DIR = env("TEMP")
if TEMP_DIR == "" then TEMP_DIR = SCRIPT_DIR end
local LOG_FILE = TEMP_DIR .. "\\en-ecoute.log"
local PID_FILE = TEMP_DIR .. "\\en-ecoute.pid"

-- ShellExecute renvoie une valeur <= 32 en cas d'échec (fichier absent...)
local function run(exe, params, show)
  if shell32 then
    local ok, rc = pcall(function()
      return shell32.ShellExecuteW(nil, wide("open"), wide(exe),
        params and wide(params) or nil, wide(SCRIPT_DIR), show or SW_HIDE)
    end)
    if ok then return tonumber(ffi.cast("intptr_t", rc)) > 32 end
  end
  os.execute(string.format('cmd /c start "" /B "%s" %s', exe, params or ""))
  return true
end

-- ---------------------------------------------------------------------------
-- Python
-- ---------------------------------------------------------------------------

-- Les pythonw.exe posés directement dans WindowsApps sont des alias de
-- 0 octet : on vise d'abord le vrai dossier du Microsoft Store.
local function python_candidates()
  local list, seen = {}, {}
  local home = env("USERPROFILE")
  local localapp = env("LOCALAPPDATA")
  local versions = { "3.15", "3.14", "3.13", "3.12", "3.11", "3.10", "3.9" }

  local function add(path, force)
    if path == "" or seen[path:lower()] then return end
    if force or file_exists(path) then
      seen[path:lower()] = true
      table.insert(list, path)
    end
  end

  for _, v in ipairs(versions) do
    add(localapp .. "\\Microsoft\\WindowsApps\\PythonSoftwareFoundation.Python."
      .. v .. "_qbz5n2kfra8p0\\pythonw.exe")
  end
  for _, v in ipairs(versions) do
    local d = v:gsub("%.", "")
    add(localapp .. "\\Programs\\Python\\Python" .. d .. "\\pythonw.exe")
    add(home .. "\\AppData\\Local\\Programs\\Python\\Python" .. d .. "\\pythonw.exe")
    add("C:\\Python" .. d .. "\\pythonw.exe")
    add("C:\\Program Files\\Python" .. d .. "\\pythonw.exe")
  end
  for dir in env("PATH"):gmatch("([^;]+)") do
    add(dir:gsub("[\\/]+$", "") .. "\\pythonw.exe")
  end
  add(localapp .. "\\Microsoft\\WindowsApps\\pythonw.exe", true)
  add("pythonw.exe", true)
  return list
end

-- ---------------------------------------------------------------------------
-- Pont : démarrage / arrêt
-- ---------------------------------------------------------------------------

local function log_size()
  local fh = open_file(LOG_FILE, "rb")
  if not fh then return -1 end
  local size = fh:seek("end")
  fh:close()
  return size
end

local function bridge_params()
  local p = string.format('"%s" --obs --port %d --cacher-pause %s --secours %s --maj %s',
    BRIDGE_PY, cfg.port, cfg.cacher_pause and "oui" or "non", cfg.secours and "oui" or "non",
    cfg.maj ~= "" and cfg.maj or "demander")
  -- les guillemets casseraient la ligne de commande
  local fmt = (cfg.format or ""):gsub('"', "")
  if fmt ~= "" then p = p .. string.format(' --format "%s"', fmt) end
  if cfg.sortie ~= "" then
    p = p .. string.format(' --sortie "%s"', cfg.sortie:gsub("/", "\\"):gsub("\\+$", ""))
  end
  return p
end

local try_list, try_index, log_before
local attempt_next

local function verify_attempt()
  -- le pont écrit une ligne dès son démarrage : le journal a donc grossi
  if log_size() ~= log_before then
    started = true
    starting = false
    obs.script_log(obs.LOG_INFO, "Pont démarré avec : " .. tostring(cfg.python))
    if settings_ref ~= nil then
      obs.obs_data_set_string(settings_ref, "python", cfg.python)
    end
    return
  end
  attempt_next()
end

attempt_next = function()
  try_index = try_index + 1
  local exe = try_list[try_index]
  if exe == nil then
    started = false
    starting = false
    obs.script_log(obs.LOG_WARNING,
      "Aucun Python fonctionnel trouvé. Installe Python (python.org ou Microsoft Store) "
      .. "puis clique sur Redémarrer le pont, ou colle le chemin de pythonw.exe dans le champ Python.")
    return
  end

  log_before = log_size()
  if not run(exe, bridge_params(), SW_HIDE) then
    return attempt_next()
  end
  cfg.python = exe
  obs.timer_add(function()
    obs.remove_current_callback()
    verify_attempt()
  end, 4000)
end

local function start_bridge()
  if started or starting then return end
  if not file_exists(BRIDGE_PY) then
    obs.script_log(obs.LOG_ERROR, "pont.py introuvable à côté du script : " .. BRIDGE_PY)
    return
  end
  try_list = {}
  if cfg.python ~= nil and cfg.python ~= "" then
    table.insert(try_list, cfg.python)
  end
  for _, c in ipairs(python_candidates()) do
    if c ~= cfg.python then table.insert(try_list, c) end
  end
  try_index = 0
  starting = true
  bridge_signature = bridge_params()
  -- au rechargement du script, l'ancien pont peut encore tenir son verrou
  obs.timer_add(function()
    obs.remove_current_callback()
    attempt_next()
  end, 1500)
end

local function stop_bridge()
  local fh = open_file(PID_FILE, "r")
  if fh then
    local pid = tonumber((fh:read("*l") or ""):match("%d+") or "")
    fh:close()
    -- le filtre sur le nom évite de tuer un autre programme si Windows a
    -- réattribué ce numéro depuis l'arrêt du pont
    if pid then
      run("taskkill.exe", string.format('/F /FI "PID eq %d" /FI "IMAGENAME eq python*"', pid), SW_HIDE)
    end
  end
  started = false
  starting = false
end

local function restart_bridge()
  stop_bridge()
  obs.timer_add(function()
    obs.remove_current_callback()
    start_bridge()
  end, 1200)
end

-- ---------------------------------------------------------------------------
-- Source Navigateur
-- ---------------------------------------------------------------------------

local function url_encode(s)
  return (s:gsub("[^%w%-_%.~]", function(c) return string.format("%%%02X", c:byte()) end))
end

local function overlay_url()
  local accent = cfg.accent == "" and "auto" or cfg.accent
  return string.format(
    "http://127.0.0.1:%d/?layout=%s&theme=%s&opacity=%d&align=%s&accent=%s"
    .. "&art=%d&progress=%d&time=%d&scale=%s&demo=%d&label=%s",
    cfg.port, cfg.layout, cfg.theme, cfg.opacity or 100, cfg.align, url_encode(accent),
    cfg.show_art and 1 or 0, cfg.show_progress and 1 or 0, cfg.show_time and 1 or 0,
    string.format("%.2f", cfg.scale or 1), cfg.demo and 1 or 0, url_encode(cfg.label or ""))
end

-- Hauteur minimale par disposition à l'échelle 1 ; la largeur laisse de la
-- place aux titres longs.
local function size_for(layout)
  if layout == "card" then return 300, 430 end
  if layout == "minimal" then return 440, 100 end
  return 560, 150
end

local function source_size()
  local w, h = size_for(cfg.layout)
  local k = cfg.scale or 1
  if k > 1 then
    w, h = math.ceil(w * k), math.ceil(h * k)
  end
  return w, h
end

local function reset_scene_items(src, w, h)
  local name = obs.obs_source_get_name(src)
  if name == nil then return end
  local scenes = obs.obs_frontend_get_scenes()
  if scenes == nil then return end
  for _, scene_src in ipairs(scenes) do
    local scene = obs.obs_scene_from_source(scene_src)
    local item = obs.obs_scene_find_source_recursive(scene, name)
    if item ~= nil then
      if obs.obs_sceneitem_get_bounds_type(item) ~= obs.OBS_BOUNDS_NONE then
        local b = obs.vec2()
        b.x, b.y = w, h
        obs.obs_sceneitem_set_bounds(item, b)
      else
        local s = obs.vec2()
        s.x, s.y = 1.0, 1.0
        obs.obs_sceneitem_set_scale(item, s)
      end
    end
  end
  obs.source_list_release(scenes)
end

-- Toute source navigateur qui pointe sur le pont reçoit les réglages, même
-- créée à la main : ils voyagent dans l'URL.
local function refresh_overlay_sources(with_size, old_port)
  local url = overlay_url()
  local w, h = source_size()
  local ports = { cfg.port }
  if old_port and old_port ~= cfg.port then table.insert(ports, old_port) end
  local count = 0

  local sources = obs.obs_enum_sources()
  if sources == nil then return 0 end
  for _, src in ipairs(sources) do
    if obs.obs_source_get_unversioned_id(src) == "browser_source" then
      local cur = obs.obs_source_get_settings(src)
      local old = obs.obs_data_get_string(cur, "url") or ""
      obs.obs_data_release(cur)
      local match = false
      for _, port in ipairs(ports) do
        if old:find("127.0.0.1:" .. port, 1, true) or old:find("localhost:" .. port, 1, true) then
          match = true
        end
      end
      if match then
        if old ~= url or with_size then
          local s = obs.obs_data_create()
          obs.obs_data_set_string(s, "url", url)
          if with_size then
            obs.obs_data_set_int(s, "width", w)
            obs.obs_data_set_int(s, "height", h)
          end
          obs.obs_source_update(src, s)
          obs.obs_data_release(s)
          if with_size then reset_scene_items(src, w, h) end
        end
        count = count + 1
      end
    end
  end
  obs.source_list_release(sources)
  return count
end

local function add_or_update_source()
  local adopted = refresh_overlay_sources(true)
  if adopted > 0 then
    obs.script_log(obs.LOG_INFO, adopted .. " source(s) mise(s) à jour : " .. overlay_url())
    return
  end

  local scene_source = obs.obs_frontend_get_current_scene()
  if scene_source == nil then
    obs.script_log(obs.LOG_WARNING, "Aucune scène active.")
    return
  end
  local w, h = source_size()
  local settings = obs.obs_data_create()
  obs.obs_data_set_string(settings, "url", overlay_url())
  obs.obs_data_set_int(settings, "width", w)
  obs.obs_data_set_int(settings, "height", h)
  obs.obs_data_set_bool(settings, "reroute_audio", false)
  obs.obs_data_set_string(settings, "css", "body { background: transparent; margin: 0; overflow: hidden; }")

  local source = obs.obs_source_create("browser_source", SOURCE_NAME, settings, nil)
  obs.obs_scene_add(obs.obs_scene_from_source(scene_source), source)
  obs.obs_data_release(settings)
  obs.obs_source_release(source)
  obs.obs_source_release(scene_source)
  obs.script_log(obs.LOG_INFO, "Source « " .. SOURCE_NAME .. " » ajoutée à la scène active.")
end

local refresh_pending = false
local function schedule_refresh(old_port)
  if refresh_pending then return end
  refresh_pending = true
  obs.timer_add(function()
    obs.remove_current_callback()
    refresh_pending = false
    refresh_overlay_sources(false, old_port)
  end, 700)
end

-- les réglages du pont passent par sa ligne de commande : on le relance,
-- en groupant les frappes successives dans un champ texte
local restart_pending = false
local function schedule_restart()
  if restart_pending then return end
  restart_pending = true
  obs.timer_add(function()
    obs.remove_current_callback()
    restart_pending = false
    if bridge_params() ~= bridge_signature then
      obs.script_log(obs.LOG_INFO, "Réglages du pont modifiés, redémarrage.")
      restart_bridge()
    end
  end, 1500)
end

-- ---------------------------------------------------------------------------
-- Panneau du script
-- ---------------------------------------------------------------------------

local function read_version()
  local fh = open_file(SCRIPT_DIR .. "version.txt", "r")
  if not fh then return "?" end
  local v = (fh:read("*l") or "?"):gsub("%s+", "")
  fh:close()
  return v
end

-- la vérification tourne dans un Python à part : OBS ne reste pas bloqué
-- pendant l'appel à GitHub, et la réponse arrive dans une fenêtre
local function check_updates()
  local exe = cfg.python
  if exe == nil or exe == "" then exe = python_candidates()[1] end
  if exe == nil then
    obs.script_log(obs.LOG_WARNING, "Python introuvable, vérification impossible.")
    return
  end
  run(exe, string.format('"%s" --verifier-maj --port %d', BRIDGE_PY, cfg.port), SW_HIDE)
end

local function output_dir()
  if cfg.sortie ~= "" then return (cfg.sortie:gsub("/", "\\")) end
  return SCRIPT_DIR .. "sortie"
end

function script_description()
  return "<h2>En Écoute <small>" .. read_version() .. "</small></h2>" .. [[
<p>Affiche le morceau joué dans le navigateur sur <b>Deezer</b>, <b>Spotify</b>,
<b>YouTube Music</b> ou <b>Apple Music</b>.</p>
<ol>
<li>Installe l'extension dans ton navigateur (bouton <b>Ouvrir le dossier de l'extension</b>, détails dans le README).</li>
<li>Clique sur <b>Ajouter la source dans la scène active</b>.</li>
</ol>
<p>Le pont démarre et s'arrête avec OBS. Le champ Python se remplit tout seul.</p>]]
end

function script_defaults(settings)
  obs.obs_data_set_default_string(settings, "python", "")
  obs.obs_data_set_default_int(settings, "port", PORT_DEFAUT)
  obs.obs_data_set_default_bool(settings, "autostart", true)
  obs.obs_data_set_default_bool(settings, "cacher_pause", true)
  obs.obs_data_set_default_string(settings, "format", "{artiste} - {titre}")
  obs.obs_data_set_default_string(settings, "sortie", "")
  obs.obs_data_set_default_bool(settings, "secours", false)
  obs.obs_data_set_default_string(settings, "maj", "demander")
  obs.obs_data_set_default_string(settings, "layout", "bar")
  obs.obs_data_set_default_string(settings, "theme", "dark")
  obs.obs_data_set_default_int(settings, "opacity", 100)
  obs.obs_data_set_default_string(settings, "align", "left")
  obs.obs_data_set_default_string(settings, "accent", "auto")
  obs.obs_data_set_default_string(settings, "label", "")
  obs.obs_data_set_default_bool(settings, "show_art", true)
  obs.obs_data_set_default_bool(settings, "show_progress", true)
  obs.obs_data_set_default_bool(settings, "show_time", true)
  obs.obs_data_set_default_double(settings, "scale", 1.0)
end

local function size_info_text(w, h)
  return string.format("Taille de la source : %d x %d px", w, h)
end

local function refresh_size_info(props, prop, settings)
  local w, h = size_for(obs.obs_data_get_string(settings, "layout"))
  local k = obs.obs_data_get_double(settings, "scale")
  if k ~= nil and k > 1 then
    w, h = math.ceil(w * k), math.ceil(h * k)
  end
  local ip = obs.obs_properties_get(props, "size_info")
  if ip ~= nil then obs.obs_property_set_description(ip, size_info_text(w, h)) end
  return true
end

function script_properties()
  local p = obs.obs_properties_create()

  -- Apparence -------------------------------------------------------------
  local look = obs.obs_properties_create()
  local layout = obs.obs_properties_add_list(look, "layout", "Disposition",
    obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
  obs.obs_property_list_add_string(layout, "Barre horizontale", "bar")
  obs.obs_property_list_add_string(layout, "Carte verticale", "card")
  obs.obs_property_list_add_string(layout, "Minimal", "minimal")
  obs.obs_property_set_modified_callback(layout, refresh_size_info)

  local iw, ih = source_size()
  obs.obs_properties_add_text(look, "size_info", size_info_text(iw, ih), obs.OBS_TEXT_INFO)

  local theme = obs.obs_properties_add_list(look, "theme", "Thème",
    obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
  obs.obs_property_list_add_string(theme, "Sombre", "dark")
  obs.obs_property_list_add_string(theme, "Clair", "light")
  obs.obs_property_list_add_string(theme, "Sans fond (texte seul)", "clean")
  obs.obs_properties_add_int_slider(look, "opacity", "Opacité du fond (%)", 0, 100, 5)

  local align = obs.obs_properties_add_list(look, "align", "Alignement",
    obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
  obs.obs_property_list_add_string(align, "Gauche", "left")
  obs.obs_property_list_add_string(align, "Centre", "center")
  obs.obs_property_list_add_string(align, "Droite", "right")

  obs.obs_properties_add_text(look, "accent",
    "Couleur d'accent (auto = tirée de la pochette, ou #rrggbb)", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_text(look, "label",
    "Texte du bandeau (vide = nom du service)", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_bool(look, "show_art", "Afficher la pochette")
  obs.obs_properties_add_bool(look, "show_progress", "Afficher la barre de progression")
  obs.obs_properties_add_bool(look, "show_time", "Afficher les temps")
  local scale = obs.obs_properties_add_float_slider(look, "scale", "Échelle", 0.5, 2.0, 0.05)
  obs.obs_property_set_modified_callback(scale, refresh_size_info)
  obs.obs_properties_add_bool(look, "demo", "Mode démo (fausse piste pour placer l'overlay)")
  obs.obs_properties_add_group(p, "grp_look", "Apparence", obs.OBS_GROUP_NORMAL, look)

  -- Comportement ----------------------------------------------------------
  local behave = obs.obs_properties_create()
  obs.obs_properties_add_bool(behave, "cacher_pause", "Cacher quand la musique est en pause")
  obs.obs_properties_add_text(behave, "format",
    "Texte de morceau.txt ({titre} {artiste} {album} {service})", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_path(behave, "sortie",
    "Dossier des fichiers texte (vide = dossier « sortie » du script)",
    obs.OBS_PATH_DIRECTORY, nil, nil)
  obs.obs_properties_add_bool(behave, "secours",
    "Mode secours : lire aussi un navigateur sans l'extension (tout ce qui y joue remonte)")
  local maj = obs.obs_properties_add_list(behave, "maj", "Mises à jour",
    obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
  obs.obs_property_list_add_string(maj, "Me demander dans une fenêtre", "demander")
  obs.obs_property_list_add_string(maj, "Les installer toutes seules", "auto")
  obs.obs_property_list_add_string(maj, "Ne pas vérifier", "non")
  obs.obs_properties_add_group(p, "grp_behave", "Comportement", obs.OBS_GROUP_NORMAL, behave)

  -- Avancé ----------------------------------------------------------------
  local adv = obs.obs_properties_create()
  obs.obs_properties_add_text(adv, "python",
    "Python (détecté tout seul, laisse vide)", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_int(adv, "port", "Port local (le même que dans l'extension)", 1024, 65535, 1)
  obs.obs_properties_add_bool(adv, "autostart", "Démarrer le pont avec OBS")
  obs.obs_properties_add_group(p, "grp_adv", "Avancé", obs.OBS_GROUP_NORMAL, adv)

  -- Boutons ---------------------------------------------------------------
  obs.obs_properties_add_button(p, "btn_add", "Ajouter la source dans la scène active",
    function() add_or_update_source() return true end)
  obs.obs_properties_add_button(p, "btn_ext", "Ouvrir le dossier de l'extension",
    function() run(SCRIPT_DIR .. "extension", nil, SW_SHOWNORMAL) return false end)
  obs.obs_properties_add_button(p, "btn_out", "Ouvrir le dossier des fichiers texte",
    function()
      local dir = output_dir()
      if not file_exists(dir) then
        obs.script_log(obs.LOG_INFO, "Le dossier sera créé au démarrage du pont : " .. dir)
      else
        run(dir, nil, SW_SHOWNORMAL)
      end
      return false
    end)
  obs.obs_properties_add_button(p, "btn_maj", "Vérifier les mises à jour",
    function() check_updates() return false end)
  obs.obs_properties_add_button(p, "btn_restart", "Redémarrer le pont",
    function() restart_bridge() return false end)
  obs.obs_properties_add_button(p, "btn_log", "Ouvrir le journal",
    function() run("notepad.exe", '"' .. LOG_FILE .. '"', SW_SHOWNORMAL) return false end)

  return p
end

function script_update(settings)
  settings_ref = settings
  local old_port = cfg.port
  cfg.python = obs.obs_data_get_string(settings, "python")
  cfg.port = obs.obs_data_get_int(settings, "port")
  cfg.autostart = obs.obs_data_get_bool(settings, "autostart")
  cfg.cacher_pause = obs.obs_data_get_bool(settings, "cacher_pause")
  cfg.format = obs.obs_data_get_string(settings, "format")
  cfg.sortie = obs.obs_data_get_string(settings, "sortie")
  cfg.secours = obs.obs_data_get_bool(settings, "secours")
  cfg.maj = obs.obs_data_get_string(settings, "maj")
  cfg.layout = obs.obs_data_get_string(settings, "layout")
  cfg.theme = obs.obs_data_get_string(settings, "theme")
  cfg.opacity = obs.obs_data_get_int(settings, "opacity")
  cfg.align = obs.obs_data_get_string(settings, "align")
  cfg.accent = obs.obs_data_get_string(settings, "accent")
  cfg.label = obs.obs_data_get_string(settings, "label")
  cfg.show_art = obs.obs_data_get_bool(settings, "show_art")
  cfg.show_progress = obs.obs_data_get_bool(settings, "show_progress")
  cfg.show_time = obs.obs_data_get_bool(settings, "show_time")
  cfg.scale = obs.obs_data_get_double(settings, "scale")
  cfg.demo = obs.obs_data_get_bool(settings, "demo")

  -- au chargement d'OBS, les scènes n'existent pas encore
  if first_update then
    first_update = false
  else
    schedule_refresh(old_port)
  end

  if not cfg.autostart then
    if started or starting then stop_bridge() end
  elseif not started and not starting then
    start_bridge()
  elseif started and bridge_params() ~= bridge_signature then
    schedule_restart()
  end
end

function script_load(settings)
  script_update(settings)
end

function script_unload()
  stop_bridge()
end
