"""
Shardsmith - reforge your fate in Aldor
=======================================
A save editor for Stoneshard by LazarusOne.

Copyright (c) 2026 LazarusOne. All rights reserved.
You may use Shardsmith freely, but you may not modify, re-upload or
redistribute it, in whole or in part, without permission from LazarusOne.
Official download: Nexus Mods (LazarusOne).
Fan-made tool, not affiliated with or endorsed by Ink Stains Games.

Edit gold, stats, XP, skills, inventory, caravan stash and enchantments in
your Stoneshard saves, and spawn items you've seen anywhere in your saves.

Save format (worked out from the game files):
  data.sav / *.map = zlib( JSON + md5(JSON + salt) + "\\0" )
  salt = "stOne!" + <folder path below StoneShard, joined with "!"> + "!shArd"

Backpack = 10x5 grid, purse = 5x4 grid, gold = coin stacks of up to 100.
Item sizes are read from the game's data.win (keep this script in the game
folder, or you'll be asked where data.win is).

Close the game (or be in the main menu) before saving changes.
A timestamped backup of data.sav is made every time you save.

Run:  python shardsmith.py
Only Python's standard library is used.
"""
import copy
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import struct
import sys
import tkinter as tk
import zlib
from tkinter import filedialog, messagebox, simpledialog, ttk

ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StoneShard")
SAVES = os.path.join(ROOT, "characters_v1")
# Folder of the script, or of Shardsmith.exe when it's a packaged exe
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Shardsmith")
CONFIG_PATH = os.path.join(APPDATA_DIR, "config.json")


def load_config():
    try:
        return json.load(open(CONFIG_PATH, encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(APPDATA_DIR, exist_ok=True)
        json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), indent=1)
    except OSError:
        pass


def steam_game_dirs():
    """Every place Stoneshard could be installed, found through Steam's library list."""
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(winreg.QueryValueEx(k, name)[0])
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    libs = []
    for r in roots:
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8").read())]
        except OSError:
            pass
    for d in "DEFGH":
        libs.append(f"{d}:\\SteamLibrary")
    seen, out = set(), []
    for lib in libs:
        p = os.path.normcase(os.path.join(lib, "steamapps", "common", "Stoneshard"))
        if p not in seen:
            seen.add(p)
            out.append(os.path.join(lib, "steamapps", "common", "Stoneshard"))
    return out

APP_TITLE = "Shardsmith \u2014 reforge your fate"
APP_AUTHOR = "LazarusOne"
COPYRIGHT_NOTICE = (
    "Copyright \u00a9 2026 LazarusOne. All rights reserved.\n\n"
    "You may use Shardsmith freely, but you may not modify, re-upload or redistribute it, "
    "in whole or in part, without permission from LazarusOne.\n\n"
    "Official download: Nexus Mods (LazarusOne).\n\n"
    "Fan-made tool, not affiliated with or endorsed by Ink Stains Games. "
    "Shardsmith contains no game files; pictures and names are read from your own Stoneshard install.")

BACKPACK_W, BACKPACK_H = 10, 5
STASH_W, STASH_H = 10, 4          # rows seen in real saves; kept conservative
BAG_W, BAG_H = 5, 4
GOLD_PER_CELL = 100

STAT_FIELDS = [
    ("STR", "Strength"), ("AGL", "Agility"), ("PRC", "Perception"),
    ("Vitality", "Vitality"), ("WIL", "Willpower"),
    ("LVL", "Level"), ("XP", "XP"), ("AP", "Unspent stat points (AP)"),
    ("SP", "Unspent ability points (SP)"), ("Received_XP", "XP gain % (100 = normal)"),
    ("HP", "Current HP"), ("MP", "Current Energy (MP)"),
    ("Sanity", "Sanity"), ("Morale", "Morale"),
    ("Hunger", "Hunger"), ("Thirsty", "Thirst"), ("Fatigue", "Fatigue"),
    ("Intoxication", "Intoxication"), ("Pain", "Pain"), ("Immunity", "Immunity"),
]

# Item and skill names are read at runtime from the player's own StoneShard.exe
# (nothing from the game is shipped with Shardsmith). Filled by GameData.load_text().
ITEM_NAMES = {}          # item id (lowercase)  -> English name
SKILL_NAMES = {}         # skill id (lowercase) -> English name
SKILL_BRANCH = {}        # skill id (lowercase) -> tree id from the game's skill table
STAT_NAMES = {}          # stat id (lowercase)  -> English stat name, e.g. "armor_piercing" -> "Armor Penetration"

TREE_TITLES = {"athletic": "Athletics", "combat": "Warfare", "ranged": "Ranged Weapons", "pyromancy": "Pyromancy",
               "geomancy": "Geomancy", "electromancy": "Electromancy", "arcanistics": "Arcanistics",
               "magic_mastery": "Magic Mastery", "none": "Basic Skills", "dagger": "Daggers",
               "2hsword": "Two-Handed Swords", "2haxe": "Two-Handed Axes", "2hmace": "Two-Handed Maces",
               "spear": "Spears", "dual": "Dual Wielding", "sword": "Swords", "axe": "Axes", "mace": "Maces",
               "staff": "Staves", "shield": "Shields", "armor": "Armored Combat"}
SURVIVAL_SKILLS = {"halt", "resourcefulness", "adaptability", "asceticism", "lightning_reflexes", "huntmaster",
                   "trailblazer", "will_to_survive", "first_aid", "cauterize_wounds"}
BASIC_SKILLS = {"butchering", "torch_strike", "craft", "sudden_attacks", "trap_search"}
SKILL_NAME_FIX = {"craft": "Crafting"}

# Shardsmith's own icon (original pixel art, 32x32 PNG)
APP_ICON_PNG = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABnklEQVR4Xu2Vr0tEQRDHdw+zQS4pGAwGwyGHwWCUQ0RM8sL9CYZDDGI0mkQMZq0Pg4iIHCZ5wQM55AWDmAyXDoN/gCvzYGHf/pjd2T2x+Ml3O5+Z+e4+xv5JpDkzKxKPiAeKL1zkIkWiEV9+MgQJiHFujBm6/t7cqSy65WP0KoIEeDPjvn5tEuJ87Um8H6IZCRLQUbuX9Obm2fTJaW0SfLdYZf1igMmTBWzFgc7lNdtabxu1KgkEVEAUXQFjxA7Q0afgA59A+TFQO3B1L7l9GKL1bGFGBXzjo2ILMzkDPr7299j4c+S9NRKSABzcuLvyORiIvOO8iiSBWHjWd07EKSDKXvBHxhe+qGvIW2fBe5RQ9w+QVxCbAxfkLgHs80u5AUBNIOW7TkGVnNL/uLGdseX2CuU8EsdHB7XfGwL3NznpwFSsGZCrgGlMEtmcugI0hGomYmT0adoCigqoxATUVlAnWEACIotLLedm3l7LoMKSqIcIitigFjfeARcx45f41uBdARSPfRtehs9VEDEJ4x1wAYf9BsHXMBXfKv6MH+e4oOyFu+BcAAAAAElFTkSuQmCC"


# ------------------------------------------------------------------ look & feel ----
# Shardsmith's own dark theme. All graphics are original pixel art drawn for Shardsmith.
PAL = {"bg": "#16151b", "panel": "#1e1d25", "panel2": "#27262f", "field": "#2c2b36", "border": "#3b3a48",
       "text": "#e7e5ef", "muted": "#9b98aa", "faint": "#6f6c7c", "accent": "#22a0aa", "accent_dk": "#17707a",
       "accent_hi": "#7fe9e0", "gold": "#e8c860", "select": "#2c5d62", "danger": "#ff6b6b"}
UI_ICONS = {
    "chronicle": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAg0lEQVR4XmNgoBAwwvQL8In8J8WsD5/egPUykaIJG4C7gFiXwGyGAQwDQODFkTys3pGwmYShHqsXNLyWYbgWmxjOMEB3JghgE8MbiMg24rKdIABFLanRixIboMAEYXyGYI0FbDEB8ga2cMAaC6Q4G8MAkOYb26Iwwgckhs3ggc8LFAMAw3gza5s9ObgAAAAASUVORK5CYII=",
    "character": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAe0lEQVR4XmNgoCUQ4BP5D8Jk2QHSeG1TDxjjM4QJm+kgDceWVMClQGxchmA1gCqAWC/gBcQEIk4vIPuZ5JgAaVCatuB/3pM7YBqEiQ5EkEKhjh4Mr4HEsBmC4gVcmmEAmyEURyP1DCDkfFzeoNgFjDCTSY3rD5/egPUCALnFPQ6YZjfDAAAAAElFTkSuQmCC",
    "equipment": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAmUlEQVR4XmNgGGjAiMsBAnwi/5HlPnx6g1UtVkGQ5pqWCQw2NhYMR46cAJvTUlOA1RAmdBfANKMDkIHorgIBFANwaYYBbIZguCAqKhBnvGCTg4cBNtuRwwAZIIcHhgtIBSguQNe848AJBg8HC6xmwqIVxQCfoBiiHLBl3RLsBhClGwowXIAtPcBchGwjOqBeIGJzATF5gWIAAE+BQiEM4ByVAAAAAElFTkSuQmCC",
    "inventory": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAgUlEQVR4XmNgoBAw4tIvwCfyH1nuw6c3ONViAHTNIIBNDCvApxCbHBOFQQAJA6KdhwZA4cICE4uzECPJMYtOvAKrhxuADmAKCFmA1wvXNriB9WsF7MLqOhQv1PipoChq2XQHhY9LHqcX8NmMDHAagG4jLkBxOhh4A+A5jNTUCMudAKZhJgfg8mL9AAAAAElFTkSuQmCC",
    "skills": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAhElEQVR4XmNgwAHyntz5D5KC0WQBdM0UGYYLMKJLCPCJgJ2ODXz49AZDPVgAWdOlE8vgevUsosDsBzd2gGkFDQ8Uc0EGssBEkDXiAjCDkA2DG7BlzSoUfT4hYSj8ZXNmYDUX7gVsLkD3AjIAeQfFCyPYBUw444xIQLEBGCmRFIAtaZMMAM1gUJ1xmNvjAAAAAElFTkSuQmCC",
    "advanced": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAvUlEQVR4XmNgoBAwEqtfgE/kP0zth09v4PqwGvD/SNR/RptljMiaalomwO1qqSmAG8KCzQUgzTBxZI0gEBDgA6Zbagr+g1zChGwAyGZkPkgByDZksGHDFhQ+igHINhMCIJeBvIhiADaAzRVQL6AEJobzYWBahsN/EAbZBsI9k5aAaUIuxDAAJoCuGa8XQDaj24ScBkAApwHImrNmHMCaXnAaQKxmvC4AAXw2wwCGF7D5Gx9AMYAUp+MEpLoAAP0ZWCe0VJ0ZAAAAAElFTkSuQmCC",
    "divider": "iVBORw0KGgoAAAANSUhEUgAAAGAAAAAJCAYAAADNYymqAAAAiElEQVR4XmNgGOJAgE/k/xD3wtAFoMDPe3Ln/1COBKahGvygQI+7dgLsfBA9VCNhSEYAcuDDwFCNBEalBasWDPZccC8hLAFU1EySUWHEFvjIYJGWBcOHT28YYeoHu/8YB3vgYwO4IgEW+EPJT0OyCAKlcFBgY0v5QynwhzwYDq2gIQ+GausHBgAnCz3vs9efZwAAAABJRU5ErkJggg==",
}


def pick_font(root, choices, fallback):
    from tkinter import font as tkfont
    fams = set(tkfont.families(root))
    return next((c for c in choices if c in fams), fallback)


def apply_theme(root):
    from tkinter import font as tkfont
    serif = pick_font(root, ["Palatino Linotype", "Constantia", "Book Antiqua", "Georgia", "DejaVu Serif"], "Times")
    sans = pick_font(root, ["Segoe UI", "Calibri", "Tahoma", "DejaVu Sans"], "Helvetica")
    mono = pick_font(root, ["Cascadia Mono", "Consolas", "DejaVu Sans Mono"], "Courier")
    F = {"body": (sans, 10), "small": (sans, 9), "bold": (sans, 10, "bold"), "mono": (mono, 10),
         "h1": (serif, 22, "bold"), "h2": (serif, 14, "bold"), "h3": (serif, 11, "bold"),
         "tag": (serif, 11, "italic")}
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=sans, size=10)
        except tk.TclError:
            pass
    P = PAL
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=P["bg"], foreground=P["text"], fieldbackground=P["field"],
                 bordercolor=P["border"], lightcolor=P["panel2"], darkcolor=P["bg"], troughcolor=P["bg"],
                 focuscolor=P["accent"], selectbackground=P["select"], selectforeground=P["text"],
                 insertcolor=P["text"], font=F["body"])
    st.map(".", foreground=[("disabled", P["faint"])])
    st.configure("TFrame", background=P["bg"])
    st.configure("Panel.TFrame", background=P["panel"])
    st.configure("TLabel", background=P["bg"], foreground=P["text"])
    st.configure("Panel.TLabel", background=P["panel"], foreground=P["text"])
    st.configure("PanelMuted.TLabel", background=P["panel"], foreground=P["muted"])
    st.configure("Muted.TLabel", foreground=P["muted"])
    st.configure("Title.TLabel", font=F["h2"], foreground=P["accent_hi"])
    st.configure("H3.TLabel", font=F["h3"], foreground=P["accent_hi"])
    st.configure("Gold.TLabel", font=F["h2"], foreground=P["gold"])
    st.configure("Badge.TLabel", background=P["accent_dk"], foreground="#eafffd", font=F["small"], padding=(4, 4))
    st.configure("BadgeAlt.TLabel", background="#4a3d1c", foreground="#ffe9a8", font=F["small"], padding=(4, 4))
    st.configure("BadgeOff.TLabel", background=P["panel2"], foreground=P["muted"], font=F["small"], padding=(4, 4))
    st.configure("Status.TLabel", background=P["panel"], foreground=P["muted"], padding=(10, 4), font=F["small"])
    st.configure("TLabelframe", background=P["bg"], bordercolor=P["border"], relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=P["bg"], foreground=P["accent_hi"], font=F["h3"])
    st.configure("TButton", background=P["panel2"], foreground=P["text"], bordercolor=P["border"],
                 lightcolor=P["panel2"], darkcolor=P["panel2"], padding=(12, 5), focusthickness=0)
    st.map("TButton", background=[("pressed", P["accent_dk"]), ("active", "#33323e")],
           bordercolor=[("active", P["accent"])])
    st.configure("Accent.TButton", background=P["accent"], foreground="#0b1517", font=F["bold"],
                 lightcolor=P["accent"], darkcolor=P["accent"], bordercolor=P["accent"])
    st.map("Accent.TButton", background=[("pressed", P["accent_dk"]), ("active", P["accent_hi"])])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(w, fieldbackground=P["field"], foreground=P["text"], background=P["panel2"],
                     bordercolor=P["border"], lightcolor=P["field"], darkcolor=P["field"],
                     arrowcolor=P["muted"], insertcolor=P["text"], padding=4)
        st.map(w, bordercolor=[("focus", P["accent"])], lightcolor=[("focus", P["accent"])],
               fieldbackground=[("readonly", P["field"])], foreground=[("readonly", P["text"])],
               selectbackground=[("readonly", P["field"])], selectforeground=[("readonly", P["text"])])
    st.configure("TNotebook", background=P["bg"], borderwidth=0, tabmargins=(6, 6, 6, 0))
    st.configure("TNotebook.Tab", background=P["panel"], foreground=P["muted"], padding=(14, 7),
                 bordercolor=P["border"], lightcolor=P["panel"], font=F["bold"])
    st.map("TNotebook.Tab", background=[("selected", P["panel2"])],
           foreground=[("selected", P["accent_hi"]), ("active", P["text"])],
           lightcolor=[("selected", P["accent"])])
    st.configure("Treeview", background=P["panel"], fieldbackground=P["panel"], foreground=P["text"],
                 rowheight=24, borderwidth=0, bordercolor=P["border"])
    st.map("Treeview", background=[("selected", P["select"])], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=P["panel2"], foreground=P["muted"], relief="flat",
                 font=F["small"], padding=(6, 4), bordercolor=P["border"])
    st.map("Treeview.Heading", background=[("active", "#33323e")])
    st.configure("TScrollbar", background=P["panel2"], troughcolor=P["bg"], bordercolor=P["bg"],
                 arrowcolor=P["muted"], lightcolor=P["panel2"], darkcolor=P["panel2"])
    st.map("TScrollbar", background=[("active", P["border"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=P["bg"], foreground=P["text"], indicatorbackground=P["field"],
                     indicatorforeground=P["accent_hi"], indicatorcolor=P["field"])
        st.map(w, indicatorcolor=[("selected", P["accent"])], background=[("active", P["bg"])])
    st.configure("TSeparator", background=P["border"])
    root.configure(bg=P["bg"])
    for opt, val in (("*TCombobox*Listbox.background", P["field"]), ("*TCombobox*Listbox.foreground", P["text"]),
                     ("*TCombobox*Listbox.selectBackground", P["select"]),
                     ("*TCombobox*Listbox.selectForeground", "#ffffff"), ("*TCombobox*Listbox.font", F["body"]),
                     ("*Toplevel.background", P["bg"]), ("*Text.background", P["field"]),
                     ("*Text.foreground", P["text"]), ("*Text.insertBackground", P["text"]),
                     ("*Text.selectBackground", P["select"]), ("*Text.relief", "flat")):
        root.option_add(opt, val)
    return F


def skill_id(key):
    return re.sub(r"^o_(pass_)?skill_", "", key).removesuffix("_ico").lower()


def build_skill_db(keys):
    """save key -> [name, tree, passive]. Passive skills sit right before their tree's active
    skills in the save, so they take the tree of the next active skill."""
    trees = [None] * len(keys)
    nxt = None
    for i in range(len(keys) - 1, -1, -1):
        sid = skill_id(keys[i])
        if sid in SURVIVAL_SKILLS:
            trees[i] = "Survival"
        elif sid in BASIC_SKILLS:
            trees[i] = "Basic Skills"
        elif sid in SKILL_BRANCH:
            nxt = TREE_TITLES.get(SKILL_BRANCH[sid], SKILL_BRANCH[sid].replace("_", " ").title())
            trees[i] = nxt
        else:
            trees[i] = nxt or "Other"
    db = {}
    for k, t in zip(keys, trees):
        sid = skill_id(k)
        name = SKILL_NAME_FIX.get(sid) or SKILL_NAMES.get(sid) or sid.replace("_", " ").title()
        db[k] = [name, t, k.startswith("o_pass")]
    return db

# --------------------------------------------------------------- enchants ----
FLAT = {"HP", "MP", "Block_Power", "Fire_Damage", "Frost_Damage", "Poison_Damage",
        "Shock_Damage", "Caustic_Damage", "VSN", "Bonus_Range"}
RESISTS = ["Slashing_Resistance", "Piercing_Resistance", "Blunt_Resistance", "Rending_Resistance",
           "Physical_Resistance", "Nature_Resistance", "Magic_Resistance", "Fire_Resistance",
           "Frost_Resistance", "Shock_Resistance", "Caustic_Resistance", "Poison_Resistance",
           "Arcane_Resistance", "Unholy_Resistance", "Psionic_Resistance", "Bleeding_Resistance",
           "Stun_Resistance", "Knockback_Resistance", "Pain_Resistance"]
# The game's own enchant table (scr_weapon_generation in Stoneshard 0.9.4.25):
# (stat, item type it can go on, pool, value). Pool 1 = common, 2 = rare. Values are fixed, never rolled.
# On the modbranch build Shardsmith re-reads this table from the game at startup (load_enchant_rules).
GAME_ENCHANTS = [
    ("Lifesteal", "Weapon", 2, 5), ("Manasteal", "Weapon", 2, 5), ("Bleeding_Chance", "Weapon", 2, 5),
    ("Daze_Chance", "Weapon", 2, 5), ("Stun_Chance", "Weapon", 2, 5), ("Knockback_Chance", "Weapon", 2, 5),
    ("Fire_Damage", "Weapon", 2, 1), ("Frost_Damage", "Weapon", 2, 1), ("Poison_Damage", "Weapon", 2, 1),
    ("Shock_Damage", "Weapon", 2, 1), ("Caustic_Damage", "Weapon", 2, 1), ("HP", "Armor", 2, 3),
    ("MP", "Armor", 2, 3), ("Health_Restoration", "Armor", 2, 4), ("MP_Restoration", "Armor", 2, 2),
    ("Weapon_Damage", "Weapon", 2, 4), ("Armor_Damage", "Weapon", 2, 10), ("Bodypart_Damage", "Weapon", 2, 10),
    ("Magic_Power", "Weapon", 2, 4), ("Skills_Energy_Cost", "Weapon", 2, -4), ("Spells_Energy_Cost", "Weapon", 2, -4),
    ("Cooldown_Reduction", "Weapon", 2, -4), ("PRR", "Weapon", 1, 3), ("Block_Power", "all", 1, 3),
    ("EVS", "Armor", 1, 2), ("CTA", "Weapon", 1, 3), ("Hit_Chance", "Weapon", 1, 2), ("CRT", "Weapon", 1, 3),
    ("CRTD", "Weapon", 1, 8), ("FMB", "Weapon", 1, -2), ("Armor_Piercing", "Weapon", 1, 4),
    ("Fortitude", "Armor", 1, 5), ("Healing_Received", "Armor", 1, 10), ("Slashing_Resistance", "Armor", 1, 8),
    ("Piercing_Resistance", "Armor", 1, 8), ("Blunt_Resistance", "Armor", 1, 8),
    ("Rending_Resistance", "Armor", 1, 8), ("Unholy_Resistance", "Armor", 1, 8),
    ("Stun_Resistance", "Armor", 1, 8), ("Knockback_Resistance", "Armor", 1, 8),
    ("Bleeding_Resistance", "Armor", 1, 8), ("Physical_Resistance", "Armor", 1, 3),
    ("Nature_Resistance", "Armor", 1, 5), ("Magic_Resistance", "Armor", 1, 5), ("Pain_Resistance", "Armor", 1, 5),
]
ENCHANT_RULES = {"list": list(GAME_ENCHANTS), "source": "built-in table (game 0.9.4.25)"}
SLOT_ORDER = ["o_inv_right_hand", "o_inv_left_hand", "o_inv_head", "o_inv_armor", "o_inv_gloves",
              "o_inv_boots", "o_inv_back", "o_inv_belt", "o_inv_neck", "o_inv_ring_1", "o_inv_ring_2"]
SLOT_NAMES = {"o_inv_right_hand": "Right hand", "o_inv_left_hand": "Left hand", "o_inv_head": "Head",
              "o_inv_armor": "Body", "o_inv_gloves": "Hands", "o_inv_boots": "Feet", "o_inv_back": "Cloak",
              "o_inv_belt": "Belt", "o_inv_neck": "Amulet", "o_inv_ring_1": "Ring 1", "o_inv_ring_2": "Ring 2"}
ARMOR_SLOTS = {"Head", "Chest", "Arms", "Legs", "Back", "Waist"}
JEWEL_SLOTS = {"Ring", "Amulet"}


# Rarity colours follow the game's own: Uncommon green, Rare blue, Epic orange, Curse, Unique purple, Treasure gold
RARITY_BG = {"enchanted": "#24402a", "magical": "#22324f", "epic": "#4a3a1a", "cursed": "#3a1f2a",
             "unique": "#33244a", "treasure": "#4a3f1c"}
RARITY_FG = {"enchanted": "#6fdc8c", "magical": "#7aa8ff", "epic": "#ffb83a", "cursed": "#c98a9a",
             "unique": "#b98cff", "treasure": "#e8c860"}


def rarity_tag(props):
    if not isinstance(props, dict) or not props.get("Suffix"):
        return None
    if props.get("is_cursed"):
        return "cursed"
    q = props.get("quality", 1)
    q = int(q) if isinstance(q, (int, float)) else 1
    if props.get("rarity") == "Unique" and q < 6:
        q = 6
    return {2: "enchanted", 3: "magical", 4: "epic", 5: "cursed", 6: "unique", 7: "treasure"}.get(q)


# Item stat ids (save-format identifiers). Names come from the game's text at runtime.
STAT_IDS = {"DMG", "DEF", "Rng", "Range", "PRR", "Block_Power", "Block_Recovery", "EVS", "Crit_Avoid", "FMB",
            "Hit_Chance", "Weapon_Damage", "Armor_Piercing", "Armor_Damage", "Bodypart_Damage", "CRT", "CRTD", "CTA",
            "Damage_Received", "Damage_Returned", "Fortitude", "MP", "MP_Restoration", "HP", "max_hp",
            "Health_Restoration", "Healing_Received", "Lifesteal", "Manasteal", "Magic_Power", "Miscast_Chance",
            "Miracle_Chance", "Miracle_Power", "Cooldown_Reduction", "Abilities_Energy_Cost", "Skills_Energy_Cost",
            "Spells_Energy_Cost", "Bonus_Range", "Received_XP", "Fatigue_Gain", "VSN", "STL", "Savvy",
            "Noise_Produced", "Loot_Find", "Gold_Find", "Bleeding_Chance", "Daze_Chance", "Stun_Chance",
            "Knockback_Chance", "Immob_Chance", "Stagger_Chance", "Slashing_Damage", "Piercing_Damage",
            "Blunt_Damage", "Rending_Damage", "Fire_Damage", "Shock_Damage", "Poison_Damage", "Caustic_Damage",
            "Frost_Damage", "Arcane_Damage", "Unholy_Damage", "Sacred_Damage", "Psionic_Damage"} | set(RESISTS) | {
            "Toxicity_Resistance", "Hunger_Resistance"}
STAT_NAME_FIX = {"dmg": "Damage"}
FLAT_STATS = {"DMG", "DEF", "HP", "MP", "max_hp", "Block_Power", "Rng", "Range", "VSN", "Bonus_Range",
              "Slashing_Damage", "Piercing_Damage", "Blunt_Damage", "Rending_Damage", "Fire_Damage", "Shock_Damage",
              "Poison_Damage", "Caustic_Damage", "Frost_Damage", "Arcane_Damage", "Unholy_Damage", "Sacred_Damage",
              "Psionic_Damage"}


def stat_name(key):
    k = key.lower()
    return STAT_NAME_FIX.get(k) or STAT_NAMES.get(k) or key.replace("_", " ")


def fmt_stat(key, v):
    txt = f"{v:+.4g}" if key not in ("DMG", "DEF", "Rng", "Range") else f"{v:.4g}"
    return txt + ("" if key in FLAT_STATS else "%")


def item_stats(props):
    """[(name, value text, enchant text, is_main)] for an item, main stat first."""
    ench = {}
    for st, v in current_enchants(props):
        ench[st] = ench.get(st, 0) + v
    main = props.get("Main") if isinstance(props.get("Main"), list) and len(props.get("Main")) == 2 else None
    out, done = [], set()
    if main and isinstance(main[1], (int, float)):
        out.append((stat_name(main[0]), fmt_stat(main[0], main[1]).lstrip("+"), "", True))
        done.add(main[0])
        if main[0].endswith("_Damage"):
            done.add("DMG")
    for k, v in props.items():
        if k in done or k not in STAT_IDS or isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            continue
        e = ench.get(k)
        out.append((stat_name(k), fmt_stat(k, v), f"{fmt_stat(k, e)} enchant" if e else "", False))
    return out


def item_slot(props):
    suf = str(props.get("Suffix", "")).split(" ", 1)
    return suf[1] if len(suf) == 2 else ""


def enchant_pool(props):
    """The game's enchants that fit this item (by its Metatype: Weapon or Armor; jewelry and shields are Armor)."""
    meta = props.get("Metatype")
    if meta not in ("Weapon", "Armor") or not item_slot(props):
        return []
    return [r for r in ENCHANT_RULES["list"] if r[1] in (meta, "all")]


def enchant_value(stat):
    for r in ENCHANT_RULES["list"]:
        if r[0] == stat:
            return float(r[3])
    return 0.0


def char_text(stat, val):
    return f"{stat} {'+' if val >= 0 else ''}{val:g}{'' if stat in FLAT else '%'}"


def parse_char(text):
    try:
        stat, v = text.rsplit(" ", 1)
        return stat, float(v.rstrip("%"))
    except ValueError:
        return None, 0.0


def current_enchants(props):
    out, i = [], 0
    while f"Char{i}" in props:
        stat, v = parse_char(props[f"Char{i}"])
        if stat:
            out.append((stat, v))
        i += 1
    return out


def roll_enchants(props, count, rng=random):
    """Like the game: each enchant comes from the common or the rare pool (50/50), no stat twice."""
    pool, out, used = enchant_pool(props), [], set()
    for _ in range(count):
        kind = 1 if rng.random() < 0.5 else 2
        cands = [r for r in pool if r[2] == kind and r[0] not in used] or [r for r in pool if r[0] not in used]
        if not cands:
            break
        r = rng.choice(cands)
        used.add(r[0])
        out.append((r[0], float(r[3])))
    return out


def apply_enchants(props, enchants):
    """Remove the item's old enchants (and their stat bonus), then add new ones."""
    for stat, v in current_enchants(props):
        if isinstance(props.get(stat), (int, float)):
            props[stat] = round(props[stat] - v, 6)
            if props[stat] == 0 and stat not in ("HP", "MP"):
                del props[stat]
    for k in [k for k in props if k.startswith("Char") and k[4:].isdigit()]:
        del props[k]
    for i, (stat, v) in enumerate(enchants):
        props[stat] = float(props.get(stat, 0.0)) + v
        props[f"Char{i}"] = char_text(stat, v)
    q = len(enchants) + 1
    props["quality"] = float(q)
    props["key"] = enchants[0][0] if enchants else ""
    slot = item_slot(props)
    if slot:
        props["Suffix"] = f"{q} {slot}"


# ------------------------------------------------------- game data (data.win) ----
# GameMaker texture pages are "2zoq": bzip2 around GameMaker's QOI variant.
_S2 = [v - 4 if v >= 2 else v for v in range(4)]
_S4 = [v - 16 if v >= 8 else v for v in range(16)]
_S5 = [v - 32 if v >= 16 else v for v in range(32)]


def qoi_decode(buf):
    w, h = struct.unpack_from("<HH", buf, 4)
    pos, end = 12, 12 + struct.unpack_from("<I", buf, 8)[0]
    out = bytearray(w * h * 4)
    index = [0] * 256
    r = g = b = 0
    a, run = 255, 0
    for o in range(0, w * h * 4, 4):
        if run > 0:
            run -= 1
        elif pos < end:
            b1 = buf[pos]
            pos += 1
            if (b1 & 0xC0) == 0x00:
                i = (b1 & 0x3F) << 2
                r, g, b, a = index[i], index[i + 1], index[i + 2], index[i + 3]
            elif (b1 & 0xE0) == 0x40:
                run = b1 & 0x1F
            elif (b1 & 0xE0) == 0x60:
                run = (((b1 & 0x1F) << 8) | buf[pos]) + 32
                pos += 1
            elif (b1 & 0xC0) == 0x80:
                r = (r + _S2[(b1 >> 4) & 3]) & 255
                g = (g + _S2[(b1 >> 2) & 3]) & 255
                b = (b + _S2[b1 & 3]) & 255
            elif (b1 & 0xE0) == 0xC0:
                b2 = buf[pos]
                pos += 1
                r = (r + _S5[b1 & 0x1F]) & 255
                g = (g + _S4[b2 >> 4]) & 255
                b = (b + _S4[b2 & 15]) & 255
            elif (b1 & 0xF0) == 0xE0:
                b2, b3 = buf[pos], buf[pos + 1]
                pos += 2
                r = (r + _S5[((b1 & 15) << 1) | (b2 >> 7)]) & 255
                g = (g + _S5[(b2 & 0x7C) >> 2]) & 255
                b = (b + _S5[((b2 & 3) << 3) | ((b3 & 0xE0) >> 5)]) & 255
                a = (a + _S5[b3 & 0x1F]) & 255
            else:
                if b1 & 8:
                    r = buf[pos]
                    pos += 1
                if b1 & 4:
                    g = buf[pos]
                    pos += 1
                if b1 & 2:
                    b = buf[pos]
                    pos += 1
                if b1 & 1:
                    a = buf[pos]
                    pos += 1
            i = ((r ^ g ^ b ^ a) & 63) << 2
            index[i], index[i + 1], index[i + 2], index[i + 3] = r, g, b, a
        out[o], out[o + 1], out[o + 2], out[o + 3] = r, g, b, a
    return w, h, out


def make_png(w, h, rgba):
    raw = b"".join(b"\0" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class GameData:
    """Item sizes and item pictures, read straight from the game's data.win."""

    def __init__(self):
        self.obj, self.spr, self.objspr, self.skill_ids, self.inv_ids = {}, {}, {}, set(), set()
        self.loaded, self.path, self.d = False, None, None
        self.build, self.version = None, None
        self.pages_cache = {}
        old = os.path.join(HERE, "editor_icons")
        self.cache_dir = old if os.path.isdir(old) else os.path.join(APPDATA_DIR, "icons")

    def load(self, path):
        d = self.d = open(path, "rb").read()
        chunks, p = {}, 8
        while p < len(d):
            name = d[p:p + 4].decode("latin1")
            ln = struct.unpack_from("<I", d, p + 4)[0]
            chunks[name] = (p + 8, ln)
            p += 8 + ln
        self.chunks = chunks
        # modbranch (VM) builds keep the game code in data.win; regular (YYC) builds compile it into the exe
        self.build = "modbranch" if chunks.get("CODE", (0, 0))[1] > 0 else "regular"

        def rs(ptr):
            n = struct.unpack_from("<I", d, ptr - 4)[0]
            return d[ptr:ptr + n].decode("utf-8", "replace")

        def entries(chunk):
            o = chunks[chunk][0]
            for i in range(struct.unpack_from("<I", d, o)[0]):
                a = struct.unpack_from("<I", d, o + 4 + 4 * i)[0]
                if a:
                    yield i, a, rs(struct.unpack_from("<I", d, a)[0])

        cells = lambda px: max(1, round(px / 27))
        by_index, self.sprite_addr = {}, {}
        for i, a, name in entries("SPRT"):
            w, h = struct.unpack_from("<II", d, a + 4)
            by_index[i] = (name, cells(w), cells(h))
            self.sprite_addr[name] = a
            if name.startswith("s_inv_"):
                self.spr[name] = (cells(w), cells(h))
        for _, a, name in entries("OBJT"):
            if name.startswith(("o_pass_skill_", "o_skill_")):
                self.skill_ids.add(skill_id(name))
            if name.startswith("o_inv_"):
                self.inv_ids.add(name[6:].lower())
                si = struct.unpack_from("<i", d, a + 4)[0]
                if si in by_index:
                    self.obj[name] = by_index[si][1:]
                    self.objspr[name] = by_index[si][0]
        o = chunks["TXTR"][0]
        self.pages = []
        for i in range(struct.unpack_from("<I", d, o)[0]):
            a = struct.unpack_from("<I", d, o + 4 + 4 * i)[0]
            v = struct.unpack_from("<9I", d, a)
            self.pages.append((v[6], v[2]))
        self.loaded, self.path = True, path

    def text_rows(self):
        """Yield (offset, text) for every game string. Normal builds (YYC) keep their text inside
        StoneShard.exe; modbranch builds (VM, game code in data.win) keep it in data.win's STRG chunk."""
        if "CODE" in self.chunks and self.chunks["CODE"][1] > 0 and "STRG" in self.chunks:
            d = self.d
            o = self.chunks["STRG"][0]
            for i in range(struct.unpack_from("<I", d, o)[0]):
                a = struct.unpack_from("<I", d, o + 4 + 4 * i)[0]
                n = struct.unpack_from("<I", d, a)[0]
                if 3 < n < 4000:
                    yield a, d[a + 4:a + 4 + n]
            return
        exe = os.path.join(os.path.dirname(self.path), "StoneShard.exe")
        if not os.path.isfile(exe):
            return
        d = open(exe, "rb").read()
        for m in re.finditer(rb"\0([A-Za-z0-9_'-]{2,64};[^\0]{0,4000})", d):
            yield m.start(), m.group(1)

    def load_enchant_rules(self):
        """modbranch only: read the enchant table straight from the game's code (scr_weapon_generation)."""
        if self.build != "modbranch":
            return False
        d = self.d
        o = self.chunks["STRG"][0]
        def string_at(i):
            a = struct.unpack_from("<I", d, o + 4 + 4 * i)[0]
            n = struct.unpack_from("<I", d, a)[0]
            return d[a + 4:a + 4 + n].decode("utf-8", "replace")
        co = self.chunks["CODE"][0]
        entry = None
        for i in range(struct.unpack_from("<I", d, co)[0]):
            a = struct.unpack_from("<I", d, co + 4 + 4 * i)[0]
            np_, length = struct.unpack_from("<II", d, a)
            n = struct.unpack_from("<I", d, np_ - 4)[0]
            if d[np_:np_ + n] == b"gml_GlobalScript_scr_weapon_generation":
                entry = (a + 12 + struct.unpack_from("<i", d, a + 12)[0], length)
                break
        if not entry:
            return False
        p, end = entry[0], entry[0] + entry[1]
        stack, rows = [], []
        while p < end:
            w = struct.unpack_from("<I", d, p)[0]
            op, t1, low = w >> 24, (w >> 16) & 0xF, w & 0xFFFF
            p += 4
            if op in (0xC0, 0xC1, 0xC2, 0xC3):
                if t1 in (0, 3):
                    stack.append(struct.unpack_from("<d" if t1 == 0 else "<q", d, p)[0])
                    p += 8
                elif t1 in (1, 2, 4, 5, 6):
                    v = struct.unpack_from("<i", d, p)[0]
                    stack.append(string_at(v) if t1 == 6 else (v if t1 == 2 else None))
                    p += 4
                elif t1 == 0xF:
                    stack.append(struct.unpack_from("<h", d, p - 4)[0])
            elif op == 0x84:
                stack.append(struct.unpack_from("<h", d, p - 4)[0])
            elif op == 0x45:
                if t1 != 0xF:
                    p += 4
                if stack:
                    stack.pop()
            elif op == 0xD9:
                p += 4
                args = stack[len(stack) - low:] if low else []
                del stack[len(stack) - low:]
                if low == 4:
                    val, kind, slot, stat = args          # arguments are pushed last-first
                    if (isinstance(stat, str) and slot in ("Weapon", "Armor", "all")
                            and isinstance(kind, (int, float)) and isinstance(val, (int, float))):
                        rows.append((stat, slot, int(kind), val))
                stack.append(None)
            elif op == 0xFF and t1 == 2:
                p += 4
        if len(rows) >= 20:
            ENCHANT_RULES["list"] = rows
            ENCHANT_RULES["source"] = f"read from your game ({len(rows)} enchants)"
            return True
        return False

    def detect_version(self):
        """Game version number (e.g. 0.9.4.25): the highest version-like string in the game's text."""
        found = set()
        pat = re.compile(rb"0\.\d{1,3}\.\d{1,3}\.\d{1,4}")
        if self.build == "modbranch":
            d, o = self.d, self.chunks["STRG"][0]
            for i in range(struct.unpack_from("<I", d, o)[0]):
                a = struct.unpack_from("<I", d, o + 4 + 4 * i)[0]
                n = struct.unpack_from("<I", d, a)[0]
                if 6 < n < 16 and pat.fullmatch(d[a + 4:a + 4 + n]):
                    found.add(d[a + 4:a + 4 + n].decode())
        else:
            exe = os.path.join(os.path.dirname(self.path), "StoneShard.exe")
            if os.path.isfile(exe):
                found = {m.decode() for m in re.findall(rb"\0(0\.\d{1,3}\.\d{1,3}\.\d{1,4})\0", open(exe, "rb").read())}
        return max(found, key=lambda v: tuple(int(x) for x in v.split(".")), default=None)

    def build_label(self, short=False):
        if not self.loaded:
            return "game not found"
        v = self.version or "?"
        if self.build == "modbranch":
            return "modbranch" if short else f"{v}-vm  \u2022  modbranch"
        return "regular" if short else f"{v}  \u2022  regular"

    def load_text(self):
        """Read English item/skill names from the player's own game files (cached after the first time)."""
        exe = os.path.join(os.path.dirname(self.path), "StoneShard.exe")
        parts = []
        for p in (self.path, exe):
            try:
                st = os.stat(p)
                parts.append(f"{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                parts.append("-")
        stamp = "v7:" + "|".join(parts)
        cache = os.path.join(APPDATA_DIR, "names_cache.json")
        try:
            c = json.load(open(cache, encoding="utf-8"))
            if c.get("stamp") == stamp:
                self.version = c.get("version")
                STAT_NAMES.update(c.get("stats", {}))
                ITEM_NAMES.update(c["items"])
                SKILL_NAMES.update(c["skills"])
                SKILL_BRANCH.update(c["branches"])
                return True
        except Exception:
            pass
        items = set(self.inv_ids) | {k[6:].lower() for k in self.obj}
        skills = set(self.skill_ids)
        stats = {k.lower() for k in STAT_IDS}
        stat_cands = {}
        key_re = re.compile(rb"([A-Za-z0-9_'-]{2,64});")
        cands, branches = {}, {}
        for off, raw in self.text_rows():
            m = key_re.match(raw)
            if not m:
                continue
            k = m.group(1).decode("latin1").lower()
            if k not in items and k not in skills and k not in stats:
                continue
            p = raw[m.end():].decode("utf-8", "replace").split(";")
            if k in stats and 12 <= len(p) <= 14 and any(ord(ch) > 0x3FF for ch in p[0]) and " / " not in p[0]:
                en = p[1].strip()                      # stat name row (prefix rows have "a / b / c" forms)
                if en and p[2] and "~" not in en and "#" not in en and len(en) < 40 and not en.endswith("."):
                    stat_cands.setdefault(k, []).append((off, en))
            if k not in items and k not in skills:
                continue
            if len(p) > 15 and p[0].startswith("o_") and k in skills:
                branches.setdefault(k, p[15])          # skill table row: ...;tree;...
            elif 12 <= len(p) <= 14 and any(ord(ch) > 0x3FF for ch in p[0]):
                en = p[1].strip()                      # text row: id;Russian;English;Chinese;...
                if (en and p[2] and "~" not in en and "#" not in en and len(en) < 60 and not en.endswith(".")
                        and not en.startswith(("Written by", "Produced by", "Author"))):   # skip book author lines
                    cands.setdefault(k, []).append((off, en))
        if not cands:
            return False

        def pick(keys):
            single = sorted(v[0][0] for k, v in cands.items() if k in keys and len(v) == 1)
            anchor = single[len(single) // 2] if single else 0
            return {k: min(v, key=lambda x: abs(x[0] - anchor))[1] for k, v in cands.items() if k in keys}
        sk, it = pick(skills), pick(items)   # an id can be both (e.g. "leech")
        cands_backup, cands = cands, stat_cands
        stn = pick(stats)
        cands = cands_backup
        STAT_NAMES.update(stn)
        try:
            self.version = self.detect_version()
        except Exception:
            self.version = None
        ITEM_NAMES.update(it)
        SKILL_NAMES.update(sk)
        SKILL_BRANCH.update(branches)
        try:
            os.makedirs(APPDATA_DIR, exist_ok=True)
            json.dump({"stamp": stamp, "version": self.version, "items": it, "skills": sk, "branches": branches,
                       "stats": stn},
                      open(cache, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
        return True

    def sprite_name(self, row):
        if row[0] in self.objspr:
            return self.objspr[row[0]]
        props = row[1] if len(row) > 1 and isinstance(row[1], dict) else {}
        return "s_inv_" + re.sub(r"[^a-z0-9]", "", str(props.get("idName", row[0])).lower())

    def of(self, row):
        if row[0] in self.obj:
            return self.obj[row[0]]
        return self.spr.get(self.sprite_name(row), (1, 1))

    def _page(self, i):
        import bz2
        if i not in self.pages_cache:
            if len(self.pages_cache) > 12:
                self.pages_cache.pop(next(iter(self.pages_cache)))
            off, size = self.pages[i]
            blob = self.d[off:off + size]
            if blob[:4] == b"2zoq":
                start = blob.find(b"BZh", 8, 32)
                raw = bz2.decompress(blob[start:])
            elif blob[:4] == b"fioq":
                raw = blob
            else:
                raise ValueError("unknown texture format")
            self.pages_cache[i] = qoi_decode(raw)
        return self.pages_cache[i]

    def png(self, name):
        """PNG bytes of a sprite's first frame (cached on disk next to the script)."""
        cached = os.path.join(self.cache_dir, name + ".png")
        if os.path.isfile(cached):
            return open(cached, "rb").read()
        a = self.sprite_addr.get(name)
        if a is None:
            return None
        t0, tl = self.chunks["TPAG"]
        t = None
        for off in range(a + 12, a + 400, 4):
            n = struct.unpack_from("<I", self.d, off)[0]
            if 1 <= n <= 256:
                ptrs = struct.unpack_from(f"<{n}I", self.d, off + 4)
                if all(t0 <= q < t0 + tl for q in ptrs):
                    t = ptrs[0]
                    break
        if t is None:
            return None
        sx, sy, sw, sh, tx, ty, tw, th, bw, bh, pid = struct.unpack_from("<11H", self.d, t)
        pw, ph, px = self._page(pid)
        out = bytearray(bw * bh * 4)
        cw = min(sw, tw, bw - tx)
        for y in range(min(sh, th, bh - ty)):
            src = ((sy + y) * pw + sx) * 4
            dst = ((ty + y) * bw + tx) * 4
            out[dst:dst + cw * 4] = px[src:src + cw * 4]
        data = make_png(bw, bh, out)
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(cached, "wb") as f:
                f.write(data)
        except OSError:
            pass
        return data


SIZES = GameData()


def truthy(v):
    return v not in (False, 0, 0.0, None)


def swap_weapon_sets(inventory):
    """Swap the active and the spare weapon set (flags 7/8 of each equipped hand item)."""
    n = 0
    for it in inventory:
        if not (isinstance(it, list) and len(it) == 10 and it[-1] in ("o_inv_right_hand", "o_inv_left_hand")):
            continue
        if truthy(it[7]):
            it[7], it[8] = False, True
        elif truthy(it[8]):
            it[7], it[8] = (1.0 if it[-1] == "o_inv_right_hand" else 2.0), False
        n += 1
    return n


def is_active_hand(it):
    return it[-1] in ("o_inv_right_hand", "o_inv_left_hand") and truthy(it[7])


def find_free(rows, w, h, gw, gh, sizes=SIZES, only_backpack=False):
    occ = set()
    for r in rows:
        if only_backpack and not (isinstance(r[-1], str) and r[-1] == "N/A"):
            continue
        try:
            idx = int(r[3])
        except (TypeError, ValueError, IndexError):
            continue
        if idx < 0:
            continue
        rw, rh = sizes.of(r)
        x0, y0 = idx % gw, idx // gw
        occ.update((x0 + dx, y0 + dy) for dx in range(rw) for dy in range(rh))
    for y in range(gh - h + 1):
        for x in range(gw - w + 1):
            if all((x + dx, y + dy) not in occ for dx in range(w) for dy in range(h)):
                return y * gw + x
    return None


def as_loose_row(row, idx):
    """Turn any item row (equipped, merchant, stash...) into a loose backpack/stash row."""
    r = copy.deepcopy(row)
    while len(r) < 10:
        r.insert(-1, 0.0)
    equipped = isinstance(r[-1], str) and r[-1].startswith("o_inv_")
    r[2], r[3] = 0.0, float(idx)
    if equipped:
        r[4] = 0.0
    r[7], r[8], r[-1] = False, False, "N/A"
    return r


# ---------------------------------------------------------------- format ----
def salt_for(path):
    rel = os.path.relpath(os.path.dirname(os.path.abspath(path)), ROOT)
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
    return ("stOne!" + "!".join(parts) + "!shArd").encode()


def read_file(path):
    raw = zlib.decompress(open(path, "rb").read())
    body, digest = raw[:-33], raw[-33:-1].decode()
    ok = hashlib.md5(body + salt_for(path)).hexdigest() == digest
    return json.loads(body.decode("utf-8")), ok


def encode(data, path):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    digest = hashlib.md5(body + salt_for(path)).hexdigest().encode()
    return zlib.compress(body + digest + b"\0", 9)


def write_file(data, path):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encode(data, path))
    try:
        ok = verify_as(tmp, path)   # check with the real file's salt before replacing it
    except Exception:
        ok = False
    if not ok:
        os.remove(tmp)
        raise RuntimeError("Hash check failed - nothing was written.")
    os.replace(tmp, path)


def verify_as(tmp, real_path):
    raw = zlib.decompress(open(tmp, "rb").read())
    body, digest = raw[:-33], raw[-33:-1].decode()
    json.loads(body.decode("utf-8"))
    return hashlib.md5(body + salt_for(real_path)).hexdigest() == digest


def backup(path):
    dst = path + datetime.datetime.now().strftime(".bak_%Y%m%d_%H%M%S")
    shutil.copy2(path, dst)
    return dst


# ----------------------------------------------------------------- gold ----
GOLD_PROPS = {"Material": "gold", "max_charge": 1.0, "idName": "gold", "Duration": 0.0, "is_cursed": 0.0,
              "MaxDuration": 0.0, "i_index": 0.0, "Main": [], "identified": 1.0, "charge": 1.0,
              "Effects_Duration": 0.0, "is_execute": 1.0}


def bag_gold(bag):
    return sum(r[6] for r in bag[1].get("lootList", []) if r and r[0] == "o_inv_gold")


def set_bag_gold(bag, amount):
    """Fill one purse with `amount` gold. Returns the amount that didn't fit."""
    loot = [r for r in bag[1].get("lootList", []) if r and r[0] != "o_inv_gold"]
    while amount > 0:
        idx = find_free(loot, 1, 1, BAG_W, BAG_H)
        if idx is None:
            break
        n = min(GOLD_PER_CELL, amount)
        loot.append(["o_inv_gold", dict(GOLD_PROPS), 0.0, float(idx), 6.0 if n >= 50 else 5.0,
                     1.0, float(n), 0.0, 0.0, "N/A"])
        amount -= n
    bag[1]["lootList"] = loot
    bag[1]["Stack"] = float(bag_gold(bag))
    return amount


# ------------------------------------------------------------- item library ----
def collect_items(obj, out):
    if isinstance(obj, list):
        if (len(obj) == 10 and isinstance(obj[0], str) and isinstance(obj[1], dict)
                and "idName" in obj[1]):
            out.append(obj)
        for x in obj:
            if isinstance(x, (list, dict)):
                collect_items(x, out)
    elif isinstance(obj, dict):
        for x in obj.values():
            if isinstance(x, (list, dict)):
                collect_items(x, out)


def item_label(row):
    p = row[1]
    if row[0].startswith("o_inv_"):
        return (ITEM_NAMES.get(row[0][6:].lower()) or ITEM_NAMES.get(str(p.get("idName", "")).lower())
                or str(p.get("idName", row[0][6:])).replace("_", " "))
    return str(p.get("idName", row[0]))


def item_category(p):
    slot = item_slot(p)
    if p.get("Metatype"):
        return f"{p['Metatype']} - {slot}" if slot else p["Metatype"]
    return "Item"


def build_library(paths):
    lib = {}
    for path in paths:
        try:
            data, _ = read_file(path)
        except Exception:
            continue
        rows = []
        collect_items(data, rows)
        for r in rows:
            if r[0] == "o_inv_gold":
                continue
            p = r[1]
            score = (p.get("quality", 1) == 1, not p.get("is_cursed"), p.get("identified", 1) == 1)
            name = r[0]
            if name not in lib or score > lib[name][0]:
                lib[name] = (score, r)
    return {k: v[1] for k, v in lib.items()}


def fresh_copy(row):
    r = copy.deepcopy(row)
    p = r[1]
    for k in ("HasOwner", "is_trade_item"):
        p.pop(k, None)
    if p.get("MaxDuration"):
        p["Duration"] = p["MaxDuration"]
    p["identified"] = 1.0
    if "lootList" in p:
        p["lootList"] = []
        if "Stack" in p:
            p["Stack"] = 0.0
    return r


# ------------------------------------------------------------------- GUI ----
def num(s):
    s = s.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return float(s)
    except ValueError:
        return s


# ---------------------------------------------------------------- chronicle ----
SNAPS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StoneShard_Snapshots")
CHRONICLE_DIR = os.path.join(APPDATA_DIR, "chronicle")
CHART_GOLD, CHART_LEVEL = "#b0852a", "#2ba3ac"     # validated for the dark chart surface


def skill_display_name(key):
    sid = skill_id(key)
    return SKILL_NAME_FIX.get(sid) or SKILL_NAMES.get(sid) or sid.replace("_", " ").title()


def ingame_minutes(summary):
    mo, d, h, mi = summary["ingame_key"]
    return ((mo * 30 + d) * 24 + h) * 60 + mi


def save_summary(data):
    from collections import Counter
    c = data.get("characterDataMap", {})
    t = data.get("timeDataMap", {})
    rows = [r for r in data.get("inventoryDataList", []) if isinstance(r, list) and len(r) > 1
            and isinstance(r[1], dict)]
    gold = sum(bag_gold(r) for r in rows if r[0] == "o_inv_moneybag")
    for n in range(1, 5):
        rows += [r for r in data.get(f"caravanStashDataList{n}", []) or []
                 if isinstance(r, list) and len(r) > 1 and isinstance(r[1], dict)]
    items = Counter()
    for r in rows:
        ench = current_enchants(r[1])
        items[item_label(r) + (f"  [{', '.join(char_text(s, v) for s, v in ench)}]" if ench else "")] += 1
    s = data.get("skillsDataMap", {}).get("skillsAllDataList", [])
    learned = {s[i] for i in range(0, len(s) - 4, 5) if isinstance(s[i], str) and s[i + 1] not in (0.0, False, 0)}
    mo, d = int(t.get("months", 0) or 0), int(t.get("days", 0) or 0)
    h, mi = int(t.get("hours", 0) or 0), int(t.get("minutes", 0) or 0)
    ingame = (f"Month {mo + 1}, " if mo else "") + f"Day {d + 1}, {h:02d}:{mi:02d}"
    num_ = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return {"level": num_(c.get("LVL")), "xp": num_(c.get("XP")), "gold": gold, "ingame": ingame,
            "ingame_key": (mo, d, h, mi), "location": str(c.get("locationTitleKey", "")).replace("_", " "),
            "items": items, "skills": learned,
            "stats": {k: c.get(k) for k, _ in STAT_FIELDS}}


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1100x700")
        self.data = None
        self.path = None
        self.dirty = False
        self.entries = []
        self.library = None
        self.skill_db_cache = None
        self.loaded_build = ""
        self.backpack_rows = BACKPACK_H
        self.set_window_icon()

        self.F = apply_theme(root)
        self.ui_img = {k: tk.PhotoImage(data=v) for k, v in UI_ICONS.items()}
        self.ui_img["logo"] = tk.PhotoImage(data=APP_ICON_PNG).zoom(2)

        self.header = tk.Canvas(root, height=78, bg=PAL["bg"], highlightthickness=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", lambda e: self.draw_header())

        top = ttk.Frame(root, style="Panel.TFrame", padding=(12, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Character", style="PanelMuted.TLabel").pack(side="left")
        self.char_cb = ttk.Combobox(top, state="readonly", width=28)
        self.char_cb.pack(side="left", padx=(6, 14))
        self.char_cb.bind("<<ComboboxSelected>>", lambda e: self.load_slots())
        ttk.Label(top, text="Save", style="PanelMuted.TLabel").pack(side="left")
        self.slot_cb = ttk.Combobox(top, state="readonly", width=34)
        self.slot_cb.pack(side="left", padx=(6, 10))
        ttk.Button(top, text="Load", command=self.load_save).pack(side="left")
        ttk.Button(top, text="Save changes", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(top, text="About", command=self.show_about).pack(side="right", padx=8)
        self.game_badge = ttk.Label(top, text="  Game: detecting...  ", style="BadgeOff.TLabel")
        self.game_badge.pack(side="right", padx=(0, 6))

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        self.build_stats_tab()
        self.build_equipment_tab()
        self.build_inventory_tab()
        self.build_skills_tab()
        self.build_chronicle_tab()
        self.build_raw_tab()

        self.status = tk.StringVar(value=f"Saves folder: {SAVES}")
        ttk.Label(root, textvariable=self.status, style="Status.TLabel", anchor="w").pack(fill="x", side="bottom")
        self.load_chars()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(150, self.startup_detect)

    # -------- data.win
    def ensure_sizes(self, silent=False):
        if SIZES.loaded:
            return True
        cfg = load_config()
        cands = [cfg.get("data_win", ""), os.path.join(HERE, "data.win")]
        cands += [os.path.join(d, "data.win") for d in steam_game_dirs()]
        path = next((c for c in cands if c and os.path.isfile(c)), None)
        if not path and silent:
            return False
        if not path:
            messagebox.showinfo("data.win", "Item sizes come from the game's data.win.\n"
                                            "Please pick it (in the Stoneshard game folder).")
            path = filedialog.askopenfilename(filetypes=[("data.win", "data.win"), ("All", "*.*")])
            if not path:
                return False
        try:
            self.root.config(cursor="watch")
            self.root.update()
            SIZES.load(path)
            self.status.set("Reading item and skill names from the game...")
            self.root.update()
            try:
                SIZES.load_text()
            except Exception:
                pass
            try:
                SIZES.load_enchant_rules()
            except Exception:
                pass
            self.skill_db_cache = None
        except Exception as e:
            messagebox.showerror("data.win", f"Could not read item sizes:\n{e}")
            return False
        finally:
            self.root.config(cursor="")
        if cfg.get("data_win") != path:
            cfg["data_win"] = path
            save_config(cfg)
        self.set_window_icon()
        self.update_game_badge()
        return True

    def update_game_badge(self):
        if not SIZES.loaded:
            self.game_badge.config(text="  Game: not found  ", style="BadgeOff.TLabel")
            return
        style = "Badge.TLabel" if SIZES.build == "modbranch" else "BadgeAlt.TLabel"
        self.game_badge.config(text=f"  {SIZES.build_label()}  ", style=style)
        if self.chars:
            cur = self.char_cb.current()
            self.load_chars()
            if 0 <= cur < len(self.chars):
                self.char_cb.current(cur)
                self.load_slots()

    def startup_detect(self):
        if self.ensure_sizes(silent=True):
            self.status.set(f"Detected Stoneshard {SIZES.build_label()}  ({os.path.dirname(SIZES.path)})")

    def draw_header(self):
        c = self.header
        c.delete("all")
        w, h = max(c.winfo_width(), 400), 78
        top, bot = (0x1f, 0x2b, 0x31), (0x16, 0x15, 0x1b)
        for y in range(h):
            t = y / (h - 1)
            col = "#%02x%02x%02x" % tuple(int(a + (b - a) * t) for a, b in zip(top, bot))
            c.create_line(0, y, w, y, fill=col)
        c.create_line(0, h - 1, w, h - 1, fill=PAL["accent_dk"])
        c.create_image(16, h // 2, anchor="w", image=self.ui_img["logo"])
        c.create_text(92, 24, anchor="w", text="SHARDSMITH", font=self.F["h1"], fill="#eaf7f5")
        c.create_image(94, 46, anchor="w", image=self.ui_img["divider"])
        c.create_text(196, 46, anchor="w", text="reforge your fate", font=self.F["tag"], fill=PAL["muted"])
        if self.data:
            cd = self.data.get("characterDataMap", {})
            c.create_text(w - 18, 28, anchor="e", text=str(cd.get("nameKey", "")), font=self.F["h2"],
                          fill=PAL["gold"])
            extra = ""
            if self.loaded_build:
                extra = f"  \u2022  made on {self.loaded_build}"
            mismatch = bool(self.loaded_build and SIZES.loaded and self.loaded_build != SIZES.build)
            c.create_text(w - 18, 52, anchor="e", font=self.F["small"],
                          fill="#f0b45a" if mismatch else PAL["muted"],
                          text=f"{cd.get('classKey', '')}  \u2022  level {cd.get('LVL', 0):g}" + extra
                               + ("  (game is " + SIZES.build + ")" if mismatch else "")
                               + ("  \u2022  unsaved changes" if self.dirty else ""))

    def set_window_icon(self):
        try:
            self._icon = tk.PhotoImage(data=APP_ICON_PNG).zoom(2)
            self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    # -------- character / slot pickers
    @staticmethod
    def save_build(path):
        """'modbranch' / 'regular' for a .map file (from its "compiler" field)."""
        try:
            comp = str(read_file(path)[0].get("compiler", "")).upper()
        except Exception:
            return ""
        return {"VM": "modbranch", "YYC": "regular"}.get(comp, "")

    def load_chars(self):
        self.chars = []
        if os.path.isdir(SAVES):
            for d in sorted(os.listdir(SAVES)):
                m = os.path.join(SAVES, d, "character.map")
                if os.path.isfile(m):
                    try:
                        name = read_file(m)[0].get("nameKey", "?")
                    except Exception:
                        name = "?"
                    b = self.save_build(m)
                    self.chars.append((d, f"{name} ({d})" + (f"  \u00b7  {b}" if b else "")))
        self.char_cb["values"] = [c[1] for c in self.chars]
        if self.chars:
            self.char_cb.current(0)
            self.load_slots()

    def load_slots(self):
        char = self.chars[self.char_cb.current()][0]
        base = os.path.join(SAVES, char)
        self.slots = []
        for d in sorted(os.listdir(base)):
            sav = os.path.join(base, d, "data.sav")
            if os.path.isfile(sav):
                t = datetime.datetime.fromtimestamp(os.path.getmtime(sav)).strftime("%Y-%m-%d %H:%M")
                b = self.save_build(os.path.join(base, d, "save.map"))
                mark = ""
                if b:
                    mark = f"  \u00b7  {b}"
                    if SIZES.loaded and b != SIZES.build:
                        mark += " \u26a0"
                self.slots.append((sav, f"{d}  ({t}){mark}"))
        self.slots.sort(key=lambda s: os.path.getmtime(s[0]), reverse=True)
        self.slot_cb["values"] = [s[1] for s in self.slots]
        if self.slots:
            self.slot_cb.current(0)
        if hasattr(self, "ctree"):
            self.refresh_chronicle()

    def load_save(self):
        if self.dirty and not messagebox.askyesno("Unsaved changes", "Discard unsaved changes?"):
            return
        if not self.slots:
            return
        path = self.slots[self.slot_cb.current()][0]
        try:
            data, ok = read_file(path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not read save:\n{e}")
            return
        self.ensure_sizes()
        self.data, self.path, self.dirty = data, path, False
        self.detect_backpack_rows()
        self.update_rows_info()
        self.root.title(APP_TITLE)
        self.refresh_all()
        comp = str(data.get("gameDataMap", {}).get("compiler", "")).upper()
        self.loaded_build = {"VM": "modbranch", "YYC": "regular"}.get(comp, "")
        msg = f"Loaded {os.path.relpath(path, ROOT) if path.startswith(ROOT) else path}" + (
            "" if ok else "   (warning: original hash didn't match)")
        if self.loaded_build and SIZES.loaded and self.loaded_build != SIZES.build:
            msg = (f"Note: this save was made on the {self.loaded_build} version, but your game is "
                   f"now the {SIZES.build} version.  " + msg)
        self.status.set(msg)
        self.last_load_msg = msg
        self.draw_header()

    # ================================================================ stats tab
    def build_stats_tab(self):
        f = ttk.Frame(self.nb, padding=16)
        self.nb.add(f, text=" " + "Character & Gold", image=self.ui_img["character"], compound="left")
        head = ttk.Frame(f)
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self.name_lbl = ttk.Label(head, text="Load a save to begin", style="Title.TLabel")
        self.name_lbl.pack(side="left")
        self.sub_lbl = ttk.Label(head, text="", style="Muted.TLabel")
        self.sub_lbl.pack(side="left", padx=12, pady=(4, 0))
        labels = dict(STAT_FIELDS)
        groups = (("Attributes", ["STR", "AGL", "PRC", "Vitality", "WIL"]),
                  ("Progress", ["LVL", "XP", "AP", "SP", "Received_XP"]),
                  ("Condition", ["HP", "MP", "Sanity", "Morale", "Pain", "Immunity"]),
                  ("Needs", ["Hunger", "Thirsty", "Fatigue", "Intoxication"]))
        self.stat_vars = {}
        for gi, (title, keys) in enumerate(groups):
            box = ttk.LabelFrame(f, text=f" {title} ", padding=(14, 8))
            box.grid(row=1 + gi // 2, column=gi % 2, sticky="nsew", padx=(0, 14), pady=(0, 12))
            for r, key in enumerate(keys):
                ttk.Label(box, text=labels[key]).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
                v = tk.StringVar()
                ttk.Entry(box, textvariable=v, width=12, justify="right").grid(row=r, column=1, sticky="e", pady=3)
                self.stat_vars[key] = v
            box.columnconfigure(0, weight=1)
        gold = ttk.LabelFrame(f, text=" Treasury ", padding=(14, 8))
        gold.grid(row=1, column=2, rowspan=2, sticky="nsew", pady=(0, 12))
        ttk.Label(gold, text="Crowns in all purses").pack(anchor="w")
        self.gold_var = tk.StringVar()
        ttk.Entry(gold, textvariable=self.gold_var, width=14, justify="right",
                  font=self.F["h2"]).pack(anchor="w", pady=(6, 8), fill="x")
        self.gold_cap = ttk.Label(gold, text="", style="Muted.TLabel", wraplength=220, justify="left")
        self.gold_cap.pack(anchor="w")
        for col in (0, 1, 2):
            f.columnconfigure(col, weight=1)
        bar = ttk.Frame(f)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Button(bar, text="Apply changes", style="Accent.TButton", command=self.apply_stats).pack(side="left")
        ttk.Label(bar, text="Apply here, then 'Save changes' at the top to write the save file.",
                  style="Muted.TLabel").pack(side="left", padx=12)

    def moneybags(self):
        return [it for it in self.data.get("inventoryDataList", [])
                if isinstance(it, list) and it and it[0] == "o_inv_moneybag"]

    def refresh_stats(self):
        c = self.data.get("characterDataMap", {})
        for key, v in self.stat_vars.items():
            val = c.get(key, "")
            v.set(f"{val:g}" if isinstance(val, float) else str(val))
        bags = self.moneybags()
        self.gold_var.set(f"{sum(bag_gold(b) for b in bags):g}")
        self.gold_cap.config(text=f"{len(bags)} purse(s), up to {BAG_W * BAG_H * GOLD_PER_CELL:,} each.\n"
                                  "Extra purses are added to your backpack if needed.")
        self.name_lbl.config(text=str(c.get("nameKey", "?")))
        self.sub_lbl.config(text=f"{c.get('raceKey', '')} {c.get('classKey', '')}  \u2022  level {c.get('LVL', 0):g}")

    def set_gold(self, gold):
        bags = self.moneybags()
        inv = self.data["inventoryDataList"]
        left = int(gold)
        for b in bags:
            left = set_bag_gold(b, left)
        added = 0
        while left > 0:
            tpl = bags[0] if bags else (self.get_library() or {}).get("o_inv_moneybag")
            if tpl is None:
                break
            w, h = SIZES.of(tpl)
            idx = find_free(inv, w, h, BACKPACK_W, self.backpack_rows, only_backpack=True)
            if idx is None:
                break
            nb = as_loose_row(fresh_copy(tpl), idx)
            left = set_bag_gold(nb, left)
            inv.append(nb)
            bags.append(nb)
            added += 1
        return left, added

    def apply_stats(self):
        if not self.data:
            return
        c = self.data["characterDataMap"]
        for key, v in self.stat_vars.items():
            if key in c and v.get().strip() != "":
                c[key] = num(v.get())
        try:
            gold = max(0, int(float(self.gold_var.get())))
        except ValueError:
            messagebox.showerror("Gold", "Gold must be a number.")
            return
        current = sum(bag_gold(b) for b in self.moneybags())
        if gold != current:
            left, added = self.set_gold(gold)
            msg = f"Gold set to {gold - left}."
            if added:
                msg += f" Added {added} new purse(s) to your backpack."
            if left:
                msg += f"\n{left} gold didn't fit (no room for more purses in the backpack)."
            messagebox.showinfo("Gold", msg)
        self.mark_dirty()
        self.refresh_all()

    # ============================================================ equipment tab
    # Coordinates are pixels on the game's own inventory panel sprite (s_inventory, 320x360).
    EQUIP_RECTS = {
        "o_inv_right_hand": (24, 18, 78, 153),
        "o_inv_back": (78, 18, 132, 99), "o_inv_gloves": (78, 99, 132, 153),
        "o_inv_armor": (132, 18, 186, 99), "o_inv_ring_1": (132, 99, 159, 126),
        "o_inv_ring_2": (132, 126, 159, 153), "o_inv_neck": (159, 99, 186, 153),
        "o_inv_head": (186, 18, 240, 72), "o_inv_belt": (186, 72, 240, 99), "o_inv_boots": (186, 99, 240, 153),
        "o_inv_left_hand": (240, 18, 295, 153),
    }
    GRID_ORIGIN = (24, 169)
    SWAP_BTN = (24, 154, 45, 168)

    def build_equipment_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text=" " + "Equipment", image=self.ui_img["equipment"], compound="left")
        try:
            sh = self.root.winfo_screenheight()
        except tk.TclError:
            sh = 1080
        self.eq_scale = 2.0 if sh >= 950 else 1.5
        if self.eq_scale == 2.0:
            self.root.geometry("1180x880")
        self.eq_screen_h = sh
        w, h = int(320 * self.eq_scale), int(360 * self.eq_scale)
        holder = ttk.Frame(f)
        holder.pack(side="left", fill="y", anchor="n")
        self.eq_canvas = tk.Canvas(holder, width=w, height=h, bg=PAL["bg"], highlightthickness=0)
        self.eq_canvas.pack(side="left", fill="y", anchor="n")
        self.eq_scroll = ttk.Scrollbar(holder, orient="vertical", command=self.eq_canvas.yview)
        self.eq_canvas.configure(yscrollcommand=self.eq_scroll.set)
        self.eq_canvas.bind("<MouseWheel>", lambda e: self.eq_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.eq_canvas.bind("<Button-4>", lambda e: self.eq_canvas.yview_scroll(-1, "units"))
        self.eq_canvas.bind("<Button-5>", lambda e: self.eq_canvas.yview_scroll(1, "units"))
        self.eq_base = {}
        self.eq_canvas.bind("<Button-1>", self.eq_click)
        self.eq_canvas.bind("<Double-1>", lambda e: self.enchant_item() if self.eq_sel_row is not None else None)
        self.eq_canvas.bind("<Motion>", self.eq_hover)
        self.eq_canvas.bind("<Leave>", lambda e: self.eq_canvas.delete("tip"))
        side = ttk.Frame(f, padding=(12, 0))
        side.pack(side="left", fill="both", expand=True)
        self.eq_title = ttk.Label(side, text="Click an item", style="Title.TLabel")
        self.eq_title.pack(anchor="w")
        self.eq_info = tk.Text(side, width=46, height=14, wrap="word", borderwidth=0, highlightthickness=0,
                               bg=PAL["bg"], fg=PAL["text"], font=self.F["body"], cursor="arrow",
                               tabs=(self.F["body"][1] * 19,), padx=0, pady=2)
        self.eq_info.pack(anchor="w", pady=(4, 10), fill="x")
        self.eq_info.tag_configure("muted", foreground=PAL["muted"])
        self.eq_info.tag_configure("main", font=self.F["h3"], foreground=PAL["gold"])
        self.eq_info.tag_configure("ench", foreground="#6fdc8c")
        self.eq_info.tag_configure("head", font=self.F["bold"], foreground=PAL["accent_hi"], spacing1=6)
        self.eq_info.tag_configure("bad", foreground="#ff8a8a")
        self.eq_info.config(state="disabled")
        grid = ttk.Frame(side)
        grid.pack(anchor="w")
        for n, (text, cmd) in enumerate((("Enchant...", self.enchant_item), ("Edit item...", self.edit_item),
                                         ("Repair", self.repair_selected), ("Duplicate", self.duplicate_item),
                                         ("Move to stash", lambda: self.move_item("stash")),
                                         ("Delete item", self.delete_item))):
            ttk.Button(grid, text=text, command=cmd, width=16).grid(row=n // 2, column=n % 2, padx=(0, 6), pady=2,
                                                                    sticky="w")
        ttk.Separator(side).pack(fill="x", pady=10)
        ttk.Label(side, text="Weapon sets", style="H3.TLabel").pack(anchor="w")
        self.eq_sets = ttk.Label(side, text="", justify="left", wraplength=320)
        self.eq_sets.pack(anchor="w", pady=4)
        ttk.Button(side, text="Switch weapon set", command=self.switch_sets, width=20).pack(anchor="w", pady=2)
        ttk.Label(side, text="(or click the swap button on the panel)", style="Muted.TLabel").pack(anchor="w")
        ttk.Separator(side).pack(fill="x", pady=10)
        ttk.Label(side, text="Double-click an item to enchant it.\nItem pictures are read from the game "
                             "the first time;\nafter that they load instantly.",
                  style="Muted.TLabel", justify="left").pack(anchor="w")
        self.eq_images, self.eq_queue, self.eq_sel_row, self.eq_boxes = {}, [], None, []
        self.eq_loading = False

    def eq_photo(self, sprite):
        """PhotoImage for a sprite at the panel's scale, or None while still loading."""
        if sprite in self.eq_images:
            return self.eq_images[sprite]
        if sprite not in self.eq_queue:
            self.eq_queue.append(sprite)
        if not self.eq_loading:
            self.eq_loading = True
            self.root.after(10, self.eq_load_next)
        return None

    def eq_panel(self, rows):
        """The game's inventory panel, stretched to the backpack's real number of rows (inventory mods)."""
        key = f"s_inventory@{rows}"
        if key in self.eq_images:
            return self.eq_images[key]
        if self.eq_photo("s_inventory") is None:
            return None
        base = self.eq_base.get("s_inventory")
        if base is None:
            return None
        if rows <= BACKPACK_H:
            img = base
        else:
            gy = self.GRID_ORIGIN[1]
            row_top, grid_end = gy + (BACKPACK_H - 1) * 27, gy + BACKPACK_H * 27
            img = tk.PhotoImage(width=320, height=360 + (rows - BACKPACK_H) * 27)
            img.tk.call(img, "copy", base, "-from", 0, 0, 320, grid_end, "-to", 0, 0)
            for k in range(rows - BACKPACK_H):
                img.tk.call(img, "copy", base, "-from", 0, row_top, 320, grid_end, "-to", 0, grid_end + k * 27)
            img.tk.call(img, "copy", base, "-from", 0, grid_end, 320, 360, "-to", 0, gy + rows * 27)
        scaled = img.zoom(2) if self.eq_scale == 2.0 else img.zoom(3).subsample(2)
        self.eq_images[key] = scaled
        return scaled

    def detect_backpack_rows(self):
        """10 x 5 normally; inventory mods add rows. Uses the manual setting, else the largest seen."""
        cfg = load_config()
        manual = cfg.get("backpack_rows")
        used = BACKPACK_H
        for r in self.data.get("inventoryDataList", []) if self.data else []:
            if isinstance(r, list) and len(r) == 10 and r[-1] == "N/A":
                try:
                    used = max(used, int(r[3]) // BACKPACK_W + SIZES.of(r)[1])
                except (TypeError, ValueError):
                    pass
        used = min(used, 30)
        seen = int(cfg.get("backpack_rows_seen", BACKPACK_H))
        if used > seen:
            cfg["backpack_rows_seen"] = used
            save_config(cfg)
            seen = used
        self.backpack_rows = int(manual) if manual else max(BACKPACK_H, seen)
        return self.backpack_rows

    def eq_load_next(self):
        if not self.eq_queue:
            self.eq_loading = False
            self.draw_equipment()
            return
        sprite = self.eq_queue.pop(0)
        img = None
        try:
            data = SIZES.png(sprite)
            if data:
                import base64
                base = tk.PhotoImage(data=base64.b64encode(data).decode())
                self.eq_base[sprite] = base
                img = base.zoom(2) if self.eq_scale == 2.0 else base.zoom(3).subsample(2)
        except Exception:
            img = None
        self.eq_images[sprite] = img
        if self.eq_queue:
            self.status.set(f"Loading item pictures from the game... ({len(self.eq_queue)} left)")
        else:
            self.status.set(getattr(self, "last_load_msg", "") or "Item pictures loaded.")
        self.draw_equipment()
        self.root.after(10, self.eq_load_next)

    def entry_index(self, row):
        for i, (_, it, _) in enumerate(self.entries):
            if it is row:
                return i
        return None

    def draw_equipment(self):
        c = self.eq_canvas
        c.delete("all")
        self.eq_boxes = []
        if not self.data or not SIZES.loaded:
            c.create_text(20, 20, anchor="nw", fill="#ccc", text="Load a save to see your equipment.")
            return
        rows = self.backpack_rows
        panel_h = 360 + (rows - BACKPACK_H) * 27
        want = 2.0 if self.eq_screen_h >= 950 and panel_h * 2 <= self.eq_screen_h - 240 else 1.5
        if want != self.eq_scale:
            self.eq_scale, self.eq_images = want, {}
        s = self.eq_scale
        c.config(width=int(320 * s), scrollregion=(0, 0, int(320 * s), int(panel_h * s)))
        c.config(height=min(int(panel_h * s), max(300, self.root.winfo_height() - 250)))
        if panel_h * s > int(c.cget("height")) + 2:
            self.eq_scroll.pack(side="left", fill="y")
        else:
            self.eq_scroll.pack_forget()
        bg = self.eq_panel(rows)
        if bg is not None:
            c.create_image(0, 0, anchor="nw", image=bg)
        else:
            c.create_rectangle(0, 0, 320 * s, panel_h * s, fill="#26252c", outline="")
        inv = self.data.get("inventoryDataList", [])

        def put(row, x0, y0, x1, y1, center=True):
            sprite = SIZES.sprite_name(row)
            img = self.eq_photo(sprite)
            w, h = SIZES.of(row)
            if center:
                cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
            else:
                cx, cy = (x0 + w * 27 / 2) * s, (y0 + h * 27 / 2) * s
            idx = self.entry_index(row)
            hw, hh = w * 27 * s / 2, h * 27 * s / 2
            box = (cx - hw, cy - hh, cx + hw, cy + hh)
            if row is self.eq_sel_row:
                c.create_rectangle(*box, fill="#3d4a2a", outline="#b6d36b", width=2)
            elif rarity_tag(row[1]):
                c.create_rectangle(*box, fill=RARITY_BG[rarity_tag(row[1])], outline="")
            if img is not None:
                c.create_image(cx, cy, image=img)
            else:
                c.create_text(cx, cy, text=item_label(row)[:10], fill="#aaa", font=(self.F["body"][0], 7))
            if idx is not None:
                self.eq_boxes.append((box, idx))
            ch = row[1].get("charge")
            if not center and isinstance(ch, float) and ch > 1 and row[0].startswith("o_inv_"):
                c.create_text(box[2] - 3, box[3] - 2, anchor="se", text=f"{ch:g}", fill="white",
                              font=(self.F["body"][0], 8, "bold"))

        for it in inv:
            if not (isinstance(it, list) and len(it) > 1 and isinstance(it[1], dict)):
                continue
            slot = it[-1]
            if slot in self.EQUIP_RECTS:
                if slot in ("o_inv_right_hand", "o_inv_left_hand") and not is_active_hand(it):
                    continue
                put(it, *self.EQUIP_RECTS[slot])
            elif slot == "N/A":
                try:
                    idx = int(it[3])
                except (TypeError, ValueError):
                    continue
                if idx < 0:
                    continue
                gx, gy = self.GRID_ORIGIN
                x0, y0 = gx + (idx % BACKPACK_W) * 27, gy + (idx // BACKPACK_W) * 27
                put(it, x0, y0, x0, y0, center=False)
        gold = sum(bag_gold(b) for b in self.moneybags())
        c.create_text(290 * s, 161 * s, anchor="e", text=f"{gold:,.0f}", fill="#e8c860",
                      font=(self.F["body"][0], 9 if s < 2 else 11, "bold"))
        c.create_text(100 * s, 161 * s, anchor="w", text=self.char_header(), fill="#cfcfd8",
                      font=(self.F["body"][0], 8 if s < 2 else 10))
        x0, y0, x1, y1 = self.SWAP_BTN
        c.create_rectangle(x0 * s, y0 * s, x1 * s, y1 * s, outline="", fill="", tags=("swap",))
        self.eq_update_side()

    def char_header(self):
        cd = self.data.get("characterDataMap", {})
        return f"{cd.get('nameKey', '')}  -  level {cd.get('LVL', 0):g}"

    def eq_hit(self, event):
        x, y = self.eq_canvas.canvasx(event.x), self.eq_canvas.canvasy(event.y)
        for box, idx in reversed(self.eq_boxes):
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return idx
        return None

    def eq_click(self, event):
        s = self.eq_scale
        x0, y0, x1, y1 = self.SWAP_BTN
        ex, ey = self.eq_canvas.canvasx(event.x), self.eq_canvas.canvasy(event.y)
        if x0 * s <= ex <= x1 * s and y0 * s <= ey <= y1 * s:
            self.switch_sets()
            return
        idx = self.eq_hit(event)
        self.eq_sel_row = self.entries[idx][1] if idx is not None else None
        if idx is not None:
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))
        else:
            self.tree.selection_set(())
        self.draw_equipment()

    def eq_hover(self, event):
        s = self.eq_scale
        x0, y0, x1, y1 = self.SWAP_BTN
        ex, ey = self.eq_canvas.canvasx(event.x), self.eq_canvas.canvasy(event.y)
        over_swap = x0 * s <= ex <= x1 * s and y0 * s <= ey <= y1 * s
        hit = self.eq_hit(event)
        c = self.eq_canvas
        c.config(cursor="hand2" if over_swap or hit is not None else "")
        c.delete("tip")
        if hit is None:
            return
        it = self.entries[hit][1]
        st = item_stats(it[1])
        lines = [item_label(it)]
        if st:
            lines.append(f"{st[0][0]}: {st[0][1]}")
        if it[1].get("MaxDuration"):
            lines.append(f"Durability {it[1].get('Duration', 0):.0f}/{it[1]['MaxDuration']:.0f}")
        ench = current_enchants(it[1])
        if ench:
            lines.append("Enchanted: " + ", ".join(f"{stat_name(a)} {fmt_stat(a, b)}" for a, b in ench))
        tx = min(ex + 14, float(c.cget("width")) - 200)
        t = c.create_text(tx + 8, ey + 14, anchor="nw", text="\n".join(lines), fill=PAL["text"],
                          font=self.F["small"], tags=("tip",), width=220)
        bx = c.bbox(t)
        c.create_rectangle(bx[0] - 6, bx[1] - 4, bx[2] + 6, bx[3] + 4, fill=PAL["panel2"], outline=PAL["border"],
                           tags=("tip",))
        c.tag_raise(t)

    def eq_update_side(self):
        inv = self.data.get("inventoryDataList", [])
        hands = [it for it in inv if isinstance(it, list) and len(it) == 10
                 and it[-1] in ("o_inv_right_hand", "o_inv_left_hand")]

        def describe(active):
            parts = []
            for side in ("o_inv_right_hand", "o_inv_left_hand"):
                its = [it for it in hands if it[-1] == side and is_active_hand(it) == active]
                parts.append(f"{SLOT_NAMES[side]}: " + (", ".join(item_label(i) for i in its) or "empty"))
            return "\n".join(parts)
        self.eq_sets.config(text=f"In use:\n{describe(True)}\n\nSpare set:\n{describe(False)}")
        sel = self.entry_index(self.eq_sel_row) if self.eq_sel_row is not None else None
        if sel is None:
            self.eq_sel_row = None
            self.eq_title.config(text="Click an item")
            self.set_info([])
            return
        _, it, gid = self.entries[sel]
        p = it[1]
        lines = []
        if gid == "equipped":
            lines.append(f"Worn: {SLOT_NAMES.get(it[-1], it[-1])}")
        else:
            lines.append("In backpack" if gid == "backpack" else "In caravan stash")
        if p.get("MaxDuration"):
            lines.append(f"Durability: {p.get('Duration', 0):.0f} / {p['MaxDuration']:.0f}")
        if p.get("rarity"):
            lines.append(f"Rarity: {p['rarity']}  (quality {p.get('quality', 1):g})")
        if p.get("is_cursed"):
            lines.append("Cursed!")
        if it[0] == "o_inv_moneybag":
            lines.append(f"Gold: {bag_gold(it):g}")
        self.eq_title.config(text=item_label(it))
        parts = [("\n".join(lines) + "\n", "muted")]
        st = item_stats(p)
        if st:
            parts.append(("Stats\n", "head"))
            for name, val, ench_txt, main in st:
                if main:
                    parts.append((f"{name}\t{val}\n", "main"))
                else:
                    parts.append((f"{name}\t", ""))
                    parts.append((val, ""))
                    parts.append((f"   ({ench_txt})\n" if ench_txt else "\n", "ench"))
        if p.get("is_cursed") and p.get("Curse"):
            parts.append((f"Curse: {p['Curse']}\n", "bad"))
        self.set_info(parts)

    def set_info(self, parts):
        t = self.eq_info
        t.config(state="normal")
        t.delete("1.0", "end")
        for text, tag in parts:
            t.insert("end", text, tag or ())
        t.update_idletasks()
        try:
            n = t.count("1.0", "end-1c", "displaylines")
            n = n[0] if isinstance(n, tuple) else n
        except tk.TclError:
            n = int(t.index("end-1c").split(".")[0])
        t.config(height=max(3, min(28, (n or 0) + 1)))
        t.config(state="disabled")

    def repair_selected(self):
        it = self.selected_item()
        if it is None:
            return
        p = it[1]
        if p.get("MaxDuration"):
            p["Duration"] = p["MaxDuration"]
            self.mark_dirty()
            self.refresh_all()

    def switch_sets(self):
        if not self.data:
            return
        if not swap_weapon_sets(self.data["inventoryDataList"]):
            messagebox.showinfo("Weapon sets", "No weapons found in either set.")
            return
        self.mark_dirty()
        self.refresh_all()
        self.status.set("Weapon sets switched. Remember to 'Save changes'.")

    # ============================================================ chronicle tab
    def build_chronicle_tab(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text=" " + "Chronicle", image=self.ui_img["chronicle"], compound="left")
        self.chron_frame = f
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="Chronicle", style="Title.TLabel").pack(side="left")
        self.chron_sub = ttk.Label(head, text="", style="Muted.TLabel")
        self.chron_sub.pack(side="left", padx=12, pady=(4, 0))
        ttk.Button(head, text="Refresh", command=lambda: self.refresh_chronicle(force=True)).pack(side="right")

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(10, 8))
        cols = ("when", "source", "ingame", "place", "lvl", "gold")
        self.ctree = ttk.Treeview(body, columns=cols, show="headings", height=11, selectmode="extended")
        for c, t, w, a in (("when", "Saved", 130, "w"), ("source", "Source", 210, "w"), ("ingame", "In game", 140, "w"),
                           ("place", "Location", 120, "w"), ("lvl", "Lvl", 40, "e"), ("gold", "Crowns", 70, "e")):
            self.ctree.heading(c, text=t)
            self.ctree.column(c, width=w, anchor=a, stretch=c in ("source", "place"))
        self.ctree.tag_configure("slot", foreground=PAL["text"])
        self.ctree.tag_configure("backup", foreground=PAL["muted"])
        self.ctree.tag_configure("snap", foreground="#9fc7ff")
        self.ctree.tag_configure("check", foreground=PAL["accent_hi"])
        self.ctree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.ctree.yview)
        sb.pack(side="left", fill="y")
        self.ctree.configure(yscrollcommand=sb.set)
        self.ctree.bind("<<TreeviewSelect>>", lambda e: self.chron_selected())
        self.ctree.bind("<Double-1>", lambda e: self.chron_compare())

        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.pack(side="left", fill="y")
        self.chron_prev = tk.Canvas(side, width=256, height=144, bg=PAL["panel"], highlightthickness=1,
                                    highlightbackground=PAL["border"])
        self.chron_prev.pack(anchor="w")
        self.chron_info = ttk.Label(side, text="Select a version.", justify="left", wraplength=256)
        self.chron_info.pack(anchor="w", pady=(8, 8))
        for text, cmd, style in (("Compare two selected", self.chron_compare, "TButton"),
                                 ("Restore to a slot...", self.chron_restore, "Accent.TButton"),
                                 ("Make checkpoint...", self.chron_checkpoint, "TButton"),
                                 ("Delete", self.chron_delete, "TButton")):
            ttk.Button(side, text=text, command=cmd, style=style, width=24).pack(anchor="w", pady=2)
        ttk.Label(side, text="Ctrl+click two versions to compare them.", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        self.chron_chart = tk.Canvas(f, height=150, bg=PAL["bg"], highlightthickness=0)
        self.chron_chart.pack(fill="x")
        self.chron_chart.bind("<Configure>", lambda e: self.draw_chron_chart())
        self.chron_chart.bind("<Motion>", self.chron_chart_hover)
        self.chron_chart.bind("<Leave>", lambda e: self.chron_chart.delete("tip"))
        self.chron_chart.bind("<Button-1>", self.chron_chart_click)
        self.chron_points, self.chron_char, self.chron_hits = [], None, []
        self.chron_summ_cache = {}
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_chronicle())

    def current_char_id(self):
        try:
            return self.chars[self.char_cb.current()][0]
        except (AttributeError, IndexError, tk.TclError):
            return None

    def chron_summary(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_mtime, st.st_size)
        if key not in self.chron_summ_cache:
            try:
                data, _ = read_file(path)
                self.chron_summ_cache[key] = save_summary(data)
            except Exception:
                self.chron_summ_cache[key] = None
        return self.chron_summ_cache[key]

    def collect_chronicle(self, char):
        """Every version of a character: slots, Shardsmith backups, checkpoints, Save Anywhere snapshots."""
        pts = []

        def add(path, kind, tag, label, slot, folder=None, deletable=False, extra=None):
            try:
                st = os.stat(path)
            except OSError:
                return
            pts.append({"path": path, "kind": kind, "tag": tag, "label": label, "slot": slot, "time": st.st_mtime,
                        "folder": folder, "deletable": deletable, "extra": extra or {}})

        base = os.path.join(SAVES, char)
        if os.path.isdir(base):
            for slot in sorted(os.listdir(base)):
                d = os.path.join(base, slot)
                if not os.path.isdir(d):
                    continue
                add(os.path.join(d, "data.sav"), "Save slot", "slot", slot, slot, folder=d)
                for b in sorted(os.listdir(d)):
                    if b.startswith("data.sav.bak_"):
                        add(os.path.join(d, b), "Backup before an edit", "backup", slot, slot, deletable=True)
        ck = os.path.join(CHRONICLE_DIR, char)
        if os.path.isdir(ck):
            for name in sorted(os.listdir(ck)):
                d = os.path.join(ck, name)
                try:
                    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
                except Exception:
                    meta = {}
                add(os.path.join(d, "data.sav"), "Checkpoint", "check", meta.get("name", name),
                    meta.get("slot", ""), folder=d, deletable=True, extra=meta)
        sn = os.path.join(SNAPS_DIR, char)
        if os.path.isdir(sn):
            for name in sorted(os.listdir(sn)):
                files = os.path.join(sn, name, "files")
                if not os.path.isdir(files):
                    continue
                try:
                    meta = json.load(open(os.path.join(sn, name, "snapshot.json"), encoding="utf-8"))
                except Exception:
                    meta = {}
                for slot in sorted(os.listdir(files)):
                    d = os.path.join(files, slot)
                    if os.path.isdir(d):
                        add(os.path.join(d, "data.sav"), f"Save Anywhere ({meta.get('kind', 'snapshot')})",
                            "snap", slot, slot, folder=d)
        # the same file can exist in several places (a snapshot of an untouched slot): keep the best one
        rank = {"slot": 0, "check": 1, "snap": 2, "backup": 3}
        seen = {}
        for p in sorted(pts, key=lambda p: rank[p["tag"]]):
            try:
                h = hashlib.sha1(open(p["path"], "rb").read()).hexdigest()
            except OSError:
                continue
            if p["tag"] == "check":                  # checkpoints are made on purpose: always list them
                seen[p["path"]] = p
            elif h not in seen:
                seen[h] = p
        out = sorted(seen.values(), key=lambda p: p["time"])
        for p in out:
            p["sum"] = self.chron_summary(p["path"])
        return [p for p in out if p["sum"]]

    def refresh_chronicle(self, force=False):
        if not hasattr(self, "ctree"):
            return
        try:
            if self.nb.index(self.nb.select()) != self.nb.index(self.chron_frame):
                return
        except tk.TclError:
            return
        char = self.current_char_id()
        if not char or (char == self.chron_char and not force):
            return
        self.root.config(cursor="watch")
        self.root.update()
        try:
            self.chron_points = self.collect_chronicle(char)
        finally:
            self.root.config(cursor="")
        self.chron_char = char
        self.ctree.delete(*self.ctree.get_children())
        for i, p in enumerate(self.chron_points):
            s = p["sum"]
            self.ctree.insert("", "end", iid=str(i), tags=(p["tag"],), values=(
                datetime.datetime.fromtimestamp(p["time"]).strftime("%Y-%m-%d %H:%M"),
                f"{p['label']}  ·  {p['kind']}", s["ingame"], s["location"], f"{s['level']:g}",
                f"{s['gold']:,.0f}"))
        words = {"slot": ("save slot", "save slots"), "backup": ("backup", "backups"),
                 "snap": ("snapshot", "snapshots"), "check": ("checkpoint", "checkpoints")}
        counts = {}
        for p in self.chron_points:
            counts[p["tag"]] = counts.get(p["tag"], 0) + 1
        name = self.chars[self.char_cb.current()][1].split(" (")[0]
        self.chron_sub.config(text=f"{name}: {len(self.chron_points)} versions  •  " + ", ".join(
            f"{n} {words[t][n > 1]}" for t, n in sorted(counts.items(), key=lambda kv: "slot check snap backup".find(kv[0]))))
        if self.chron_points:
            last = str(len(self.chron_points) - 1)
            self.ctree.selection_set(last)
            self.ctree.see(last)
        self.chron_selected()

    def chron_sel(self):
        return [self.chron_points[int(i)] for i in self.ctree.selection() if i.isdigit()]

    def chron_selected(self):
        sel = self.chron_sel()
        c = self.chron_prev
        c.delete("all")
        self._chron_img = None
        if not sel:
            self.chron_info.config(text="Select a version.")
            self.draw_chron_chart()
            return
        p = sel[-1]
        s = p["sum"]
        prev = os.path.join(p["folder"], "preview.png") if p["folder"] else None
        if prev and os.path.isfile(prev):
            try:
                img = tk.PhotoImage(file=prev)
                k = max(1, -(-img.width() // 256), -(-img.height() // 144))
                self._chron_img = img.subsample(k) if k > 1 else img
                c.create_image(128, 72, image=self._chron_img)
            except tk.TclError:
                pass
        if self._chron_img is None:
            c.create_text(128, 72, text="no screenshot for this version", fill=PAL["faint"], font=self.F["small"])
        lines = [f"{p['label']}  ·  {p['kind']}",
                 f"{s['ingame']} at {s['location'] or '?'}",
                 f"Level {s['level']:g}  •  {s['xp']:,.0f} XP  •  {s['gold']:,.0f} crowns",
                 f"{sum(s['items'].values())} items  •  {len(s['skills'])} skills learned"]
        if p["extra"].get("note"):
            lines.append(f"Note: {p['extra']['note']}")
        self.chron_info.config(text="\n".join(lines))
        self.draw_chron_chart()

    # ---- chart: two small multiples on one timeline (one measure per chart, no dual axis)
    def draw_chron_chart(self):
        c = self.chron_chart
        c.delete("all")
        self.chron_hits = []
        pts = self.chron_points
        w = max(c.winfo_width(), 600)
        h = 150
        if len(pts) < 2:
            c.create_text(w // 2, h // 2, text="The chart appears when a character has two or more versions.",
                          fill=PAL["faint"], font=self.F["small"])
            return
        sel = {id(p) for p in self.chron_sel()}
        gap = 28
        cw = (w - gap) / 2
        for k, (title, key, color, fmt) in enumerate((("Crowns", "gold", CHART_GOLD, "{:,.0f}"),
                                                      ("Level", "level", CHART_LEVEL, "{:g}"))):
            x0 = k * (cw + gap)
            left, right, top, bot = x0 + 52, x0 + cw - 10, 34, h - 24
            vals = [p["sum"][key] for p in pts]
            lo, hi = min(vals), max(vals)
            if key == "level":
                lo = min(lo, 1)
            if hi == lo:
                hi = lo + 1
            c.create_text(x0 + 4, 8, anchor="nw", text=title, fill=PAL["text"], font=self.F["bold"])
            for v in (lo, hi):
                y = bot - (v - lo) / (hi - lo) * (bot - top)
                c.create_line(left, y, right, y, fill=PAL["panel2"])
                c.create_text(left - 6, y, anchor="e", text=fmt.format(v), fill=PAL["faint"], font=self.F["small"])
            xs = [left + i * (right - left) / (len(pts) - 1) for i in range(len(pts))]
            ys = [bot - (v - lo) / (hi - lo) * (bot - top) for v in vals]
            c.create_line(*[q for xy in zip(xs, ys) for q in xy], fill=color, width=2)
            for i, (x, y) in enumerate(zip(xs, ys)):
                big = id(pts[i]) in sel
                r = 6 if big else 4
                c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill=PAL["bg"], outline="")
                c.create_oval(x - r, y - r, x + r, y + r, fill=color,
                              outline=PAL["accent_hi"] if big else color, width=2 if big else 1)
                self.chron_hits.append((x, y, i, title, fmt.format(vals[i])))
            first = datetime.datetime.fromtimestamp(pts[0]["time"]).strftime("%d.%m %H:%M")
            last = datetime.datetime.fromtimestamp(pts[-1]["time"]).strftime("%d.%m %H:%M")
            c.create_text(left, h - 6, anchor="sw", text=first, fill=PAL["faint"], font=self.F["small"])
            c.create_text(right, h - 6, anchor="se", text=last, fill=PAL["faint"], font=self.F["small"])

    def chron_hit(self, event):
        best = None
        for x, y, i, title, txt in self.chron_hits:
            d = (x - event.x) ** 2 + (y - event.y) ** 2
            if d <= 14 ** 2 and (best is None or d < best[0]):
                best = (d, x, y, i, title, txt)
        return best

    def chron_chart_hover(self, event):
        c = self.chron_chart
        c.delete("tip")
        hit = self.chron_hit(event)
        c.config(cursor="hand2" if hit else "")
        if not hit:
            return
        _, x, y, i, title, txt = hit
        p = self.chron_points[i]
        text = f"{title}: {txt}\n{p['label']}\n{p['sum']['ingame']}"
        tx = min(x + 12, c.winfo_width() - 190)
        t = c.create_text(tx + 8, y - 8, anchor="nw", text=text, fill=PAL["text"], font=self.F["small"],
                          tags=("tip",))
        bx = c.bbox(t)
        c.create_rectangle(bx[0] - 6, bx[1] - 4, bx[2] + 6, bx[3] + 4, fill=PAL["panel2"], outline=PAL["border"],
                           tags=("tip",))
        c.tag_raise(t)

    def chron_chart_click(self, event):
        hit = self.chron_hit(event)
        if hit:
            iid = str(hit[3])
            if event.state & 0x0004:     # Ctrl: add to selection (for compare)
                self.ctree.selection_add(iid)
            else:
                self.ctree.selection_set(iid)
            self.ctree.see(iid)

    # ---- actions
    def chron_compare(self):
        sel = self.chron_sel()
        if len(sel) != 2:
            messagebox.showinfo("Compare", "Select exactly two versions (Ctrl+click) to compare them.")
            return
        a, b = sorted(sel, key=lambda p: p["time"])
        sa, sb = a["sum"], b["sum"]
        win = tk.Toplevel(self.root)
        win.title("Compare versions")
        win.geometry("640x560")
        txt = tk.Text(win, wrap="word", font=self.F["body"], padx=14, pady=10, borderwidth=0)
        txt.pack(fill="both", expand=True)
        txt.tag_configure("h", font=self.F["h3"], foreground=PAL["accent_hi"], spacing1=10, spacing3=4)
        txt.tag_configure("plus", foreground="#6fdc8c")
        txt.tag_configure("minus", foreground="#ff8a8a")
        txt.tag_configure("muted", foreground=PAL["muted"])

        def when(p):
            return datetime.datetime.fromtimestamp(p["time"]).strftime("%Y-%m-%d %H:%M")
        txt.insert("end", "From  ", "muted")
        txt.insert("end", f"{a['label']} · {a['kind']}  ({when(a)})\n")
        txt.insert("end", "To      ", "muted")
        txt.insert("end", f"{b['label']} · {b['kind']}  ({when(b)})\n")
        mins = ingame_minutes(sb) - ingame_minutes(sa)
        txt.insert("end", f"\nIn game: {sa['ingame']} → {sb['ingame']}", "muted")
        if mins > 0:
            txt.insert("end", f"  ({mins // 1440} d {mins % 1440 // 60} h later)", "muted")
        if sa["location"] != sb["location"]:
            txt.insert("end", f"\nTravelled: {sa['location']} → {sb['location']}", "muted")

        txt.insert("end", "\nCharacter\n", "h")
        changed = False
        for key, label in (("level", "Level"), ("xp", "XP"), ("gold", "Crowns")) + tuple(
                (k, v) for k, v in STAT_FIELDS if k not in ("LVL", "XP")):
            va = sa[key] if key in sa else sa["stats"].get(key)
            vb = sb[key] if key in sb else sb["stats"].get(key)
            if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)) or abs(vb - va) < 0.005:
                continue
            changed = True
            d = vb - va
            txt.insert("end", f"{label}: {va:,.4g} → {vb:,.4g}  ")
            txt.insert("end", f"({'+' if d > 0 else ''}{d:,.4g})\n", "plus" if d > 0 else "minus")
        if not changed:
            txt.insert("end", "No changes.\n", "muted")

        txt.insert("end", "Items\n", "h")
        gained = sb["items"] - sa["items"]
        lost = sa["items"] - sb["items"]
        for name, n in sorted(gained.items()):
            txt.insert("end", f"+ {name}" + (f"  ×{n}" if n > 1 else "") + "\n", "plus")
        for name, n in sorted(lost.items()):
            txt.insert("end", f"− {name}" + (f"  ×{n}" if n > 1 else "") + "\n", "minus")
        if not gained and not lost:
            txt.insert("end", "Same items.\n", "muted")

        txt.insert("end", "Skills\n", "h")
        learned, forgot = sb["skills"] - sa["skills"], sa["skills"] - sb["skills"]
        for k in sorted(learned, key=skill_display_name):
            txt.insert("end", f"+ {skill_display_name(k)}\n", "plus")
        for k in sorted(forgot, key=skill_display_name):
            txt.insert("end", f"− {skill_display_name(k)}\n", "minus")
        if not learned and not forgot:
            txt.insert("end", "No skill changes.\n", "muted")
        txt.config(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)

    def chron_restore(self):
        sel = self.chron_sel()
        if len(sel) != 1:
            messagebox.showinfo("Restore", "Select one version to restore.")
            return
        p = sel[0]
        char = self.chron_char
        base = os.path.join(SAVES, char)
        slots = sorted(d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "save.map")))
        src_map = os.path.join(p["folder"], "save.map") if p["folder"] else None
        has_map = bool(src_map and os.path.isfile(src_map))
        win = tk.Toplevel(self.root)
        win.title("Restore version")
        win.transient(self.root)
        fr = ttk.Frame(win, padding=16)
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text=f"Restore  {p['label']}  ({p['sum']['ingame']}, level {p['sum']['level']:g})",
                  style="H3.TLabel").pack(anchor="w")
        ttk.Label(fr, text="into save slot:").pack(anchor="w", pady=(10, 2))
        choices = slots + ([s for s in ("save_1", "save_2", "save_3") if s not in slots] if has_map else [])
        slot_var = tk.StringVar(value=p["slot"] if p["slot"] in choices else (choices[0] if choices else ""))
        ttk.Combobox(fr, textvariable=slot_var, values=choices, state="readonly", width=24).pack(anchor="w")
        ttk.Label(fr, text="Close the game first (or stay in its main menu).\n"
                           "The slot's current save is backed up before it's replaced,\n"
                           "so it stays in the Chronicle and can be restored too.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(10, 12))

        def go():
            slot = slot_var.get()
            if not slot:
                return
            dst_dir = os.path.join(base, slot)
            dst = os.path.join(dst_dir, "data.sav")
            try:
                data, _ = read_file(p["path"])
                os.makedirs(dst_dir, exist_ok=True)
                if os.path.isfile(dst):
                    backup(dst)
                write_file(data, dst)                     # re-signed for the target slot
                if has_map:
                    m, _ = read_file(src_map)
                    write_file(m, os.path.join(dst_dir, "save.map"))
                    prev = os.path.join(p["folder"], "preview.png")
                    if os.path.isfile(prev) and os.path.abspath(p["folder"]) != os.path.abspath(dst_dir):
                        shutil.copy2(prev, os.path.join(dst_dir, "preview.png"))
            except Exception as e:
                messagebox.showerror("Restore failed", str(e), parent=win)
                return
            win.destroy()
            self.status.set(f"Restored {p['label']} into {slot}. Load it from the game's menu.")
            if self.path and os.path.abspath(self.path) == os.path.abspath(dst):
                self.data, _ = read_file(dst)
                self.dirty = False
                self.root.title(APP_TITLE)
                self.refresh_all()
            self.load_slots()
            self.refresh_chronicle(force=True)
        bar = ttk.Frame(fr)
        bar.pack(fill="x")
        ttk.Button(bar, text="Restore", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right", padx=8)

    def chron_checkpoint(self):
        sel = self.chron_sel()
        if len(sel) != 1:
            messagebox.showinfo("Checkpoint", "Select the version you want to keep as a checkpoint.")
            return
        p = sel[0]
        name = simpledialog.askstring("Make checkpoint", "Name this checkpoint (e.g. 'before the crypt'):",
                                      parent=self.root)
        if not name:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        d = os.path.join(CHRONICLE_DIR, self.chron_char, stamp)
        try:
            os.makedirs(d, exist_ok=True)
            shutil.copy2(p["path"], os.path.join(d, "data.sav"))
            if p["folder"]:
                for extra in ("save.map", "preview.png"):
                    src = os.path.join(p["folder"], extra)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(d, extra))
            json.dump({"name": name, "slot": p["slot"], "created": stamp, "from": p["label"]},
                      open(os.path.join(d, "meta.json"), "w", encoding="utf-8"))
        except OSError as e:
            messagebox.showerror("Checkpoint", str(e))
            return
        self.status.set(f"Checkpoint '{name}' saved.")
        self.refresh_chronicle(force=True)

    def chron_delete(self):
        sel = self.chron_sel()
        if not sel:
            return
        if any(not p["deletable"] for p in sel):
            messagebox.showinfo("Delete", "Only checkpoints and Shardsmith backups can be deleted here.\n"
                                          "Save slots are managed by the game, snapshots by Save Anywhere.")
            return
        if not messagebox.askyesno("Delete", f"Delete {len(sel)} version(s) for good?"):
            return
        for p in sel:
            try:
                if p["tag"] == "check":
                    shutil.rmtree(os.path.dirname(p["path"]))
                else:
                    os.remove(p["path"])
            except OSError:
                pass
        self.refresh_chronicle(force=True)

    # ============================================================ inventory tab
    def build_inventory_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text=" " + "Inventory & Stash", image=self.ui_img["inventory"], compound="left")
        cols = ("item", "slot", "dur", "charges", "quality", "ench")
        self.tree = ttk.Treeview(f, columns=cols, show="tree headings", height=16)
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=130, stretch=False)
        self.tree.tag_configure("group", font=self.F["h3"], foreground=PAL["accent_hi"])
        for tag, color in RARITY_FG.items():
            self.tree.tag_configure(tag, foreground=color)
        for c, w, t in zip(cols, (200, 100, 80, 60, 55, 230),
                           ("Item", "Slot", "Durability", "Charges", "Quality", "Enchants")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit_item())
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        btns = ttk.Frame(f, padding=(8, 0))
        btns.pack(side="left", fill="y")
        for text, cmd in (("Spawn item...", self.spawn_dialog),
                          (None, None),
                          ("Enchant...", self.enchant_item),
                          ("Edit item...", self.edit_item),
                          ("Set charges...", self.set_charges),
                          ("Duplicate", self.duplicate_item),
                          ("Delete item", self.delete_item),
                          (None, None),
                          ("Move to stash", lambda: self.move_item("stash")),
                          ("Move to backpack", lambda: self.move_item("backpack")),
                          (None, None),
                          ("Fix enchant values", self.fix_enchants),
                          ("Repair all", self.repair_all),
                          ("Identify all", self.identify_all),
                          ("Uncurse all", self.uncurse_all)):
            if text is None:
                ttk.Separator(btns).pack(fill="x", pady=6)
            else:
                ttk.Button(btns, text=text, command=cmd, width=17).pack(pady=2)

    def stash_list(self, n=1):
        key = f"caravanStashDataList{n}"
        if key not in self.data:
            self.data[key] = []
        return self.data[key]

    def has_stash_chest(self):
        ups = self.data.get("caravanDataMap", {}).get("upgradesList", [])
        return any(str(u).startswith("Chest") for u in ups)

    def refresh_inventory(self):
        open_state = {g: (self.tree.item(g, "open") if self.tree.exists(g) else True)
                      for g in ("equipped", "backpack", "stash1", "stash2", "stash3", "stash4")}
        self.tree.delete(*self.tree.get_children())
        self.entries = []
        inv = [it for it in self.data.get("inventoryDataList", [])
               if isinstance(it, list) and len(it) > 1 and isinstance(it[1], dict)]
        equipped = [it for it in inv if isinstance(it[-1], str) and it[-1].startswith("o_inv_")]
        backpack = [it for it in inv if it not in equipped]
        order = {id(it): n for n, it in enumerate(inv)}
        equipped.sort(key=lambda it: (SLOT_ORDER.index(it[-1]) if it[-1] in SLOT_ORDER else 99,
                                      not is_active_hand(it), order[id(it)]))

        groups = [("equipped", "Equipped", equipped, self.data.get("inventoryDataList")),
                  ("backpack", "Backpack", backpack, self.data.get("inventoryDataList"))]
        for n in range(1, 5):
            lst = self.data.get(f"caravanStashDataList{n}")
            if lst is None:
                continue
            if n == 1 or lst:
                groups.append((f"stash{n}", "Caravan stash" if n == 1 else f"Caravan stash {n}", lst, lst))
        for gid, label, rows, container in groups:
            self.tree.insert("", "end", iid=gid, text=f"{label} ({len(rows)})", open=open_state.get(gid, True),
                             tags=("group",))
            for it in rows:
                p = it[1]
                name = item_label(it)
                if it[0] == "o_inv_moneybag":
                    name = f"{item_label(it)} ({bag_gold(it):g} gold)"
                if gid == "equipped":
                    slot_txt = SLOT_NAMES.get(it[-1], it[-1].replace("o_inv_", ""))
                    if it[-1] in ("o_inv_right_hand", "o_inv_left_hand"):
                        slot_txt += " (in use)" if is_active_hand(it) else " (spare)"
                else:
                    slot_txt = "backpack" if gid == "backpack" else "stash"
                dur = f"{p.get('Duration', 0):.0f}/{p.get('MaxDuration', 0):.0f}" if p.get("MaxDuration") else ""
                ch = p.get("charge", "")
                ch = f"{ch:g}" if isinstance(ch, float) and ch >= 0 else ""
                q = p.get("quality", "")
                iid = str(len(self.entries))
                self.entries.append((container, it, gid))
                self.tree.insert(gid, "end", iid=iid, tags=(rarity_tag(p) or "",), values=(
                    name, slot_txt, dur, ch, f"{q:g}" if isinstance(q, float) else "",
                    ", ".join(char_text(s, v) for s, v in current_enchants(p))))

    def selected_entry(self):
        sel = self.tree.selection()
        if not sel or not sel[0].isdigit():
            messagebox.showinfo("Inventory", "Select an item first.")
            return None
        return self.entries[int(sel[0])]

    def selected_item(self):
        e = self.selected_entry()
        return e[1] if e else None

    def place(self, row, dest):
        """Put a copy of `row` into 'backpack' or 'stash'. Returns True if it fit."""
        if not self.ensure_sizes():
            return False
        w, h = SIZES.of(row)
        if dest == "backpack":
            lst = self.data["inventoryDataList"]
            idx = find_free(lst, w, h, BACKPACK_W, self.backpack_rows, only_backpack=True)
        else:
            lst = self.stash_list(1)
            idx = find_free(lst, w, h, STASH_W, STASH_H)
        if idx is None:
            return False
        lst.append(as_loose_row(row, idx))
        return True

    def move_item(self, dest):
        e = self.selected_entry()
        if e is None:
            return
        container, it, gid = e
        if gid == "equipped":
            messagebox.showinfo("Move", "Unequip the item in game first; only loose items can be moved.")
            return
        if (dest == "stash" and gid.startswith("stash")) or (dest == "backpack" and gid == "backpack"):
            return
        if dest == "stash" and not self.has_stash_chest():
            if not messagebox.askyesno("Stash", "Your caravan doesn't seem to have a chest yet, so you "
                                                "may not be able to open the stash. Move anyway?"):
                return
        if not self.place(it, dest):
            w, h = SIZES.of(it)
            messagebox.showinfo("Move", f"No free {w}x{h} space in the {dest}.")
            return
        container.remove(it)
        self.mark_dirty()
        self.refresh_all()

    def duplicate_item(self):
        e = self.selected_entry()
        if e is None:
            return
        dest = "stash" if e[2].startswith("stash") else "backpack"
        if not self.place(fresh_copy(e[1]), dest):
            messagebox.showinfo("Duplicate", f"No free space in the {dest}.")
            return
        self.mark_dirty()
        self.refresh_all()

    def get_library(self):
        if self.library is None:
            paths = []
            for root_dir, _, files in os.walk(SAVES):
                paths += [os.path.join(root_dir, f) for f in files if f == "data.sav"]
            self.root.config(cursor="watch")
            self.root.update()
            try:
                self.library = build_library(paths)
            finally:
                self.root.config(cursor="")
        return self.library

    def spawn_dialog(self):
        if not self.data or not self.ensure_sizes():
            return
        lib = self.get_library()
        if not lib:
            messagebox.showinfo("Spawn", "No items found in your saves.")
            return
        rows = sorted(lib.values(), key=lambda r: (item_category(r[1]), item_label(r).lower()))
        win = tk.Toplevel(self.root)
        win.title("Spawn item")
        win.geometry("560x520")
        win.transient(self.root)
        ttk.Label(win, text=f"{len(rows)} different items found across all your saves "
                            "(inventories, stashes and every merchant you've visited).",
                  wraplength=520).pack(anchor="w", padx=10, pady=(10, 4))
        sf = ttk.Frame(win)
        sf.pack(fill="x", padx=10)
        ttk.Label(sf, text="Search:").pack(side="left")
        q = tk.StringVar()
        ent = ttk.Entry(sf, textvariable=q)
        ent.pack(side="left", fill="x", expand=True, padx=4)
        lt = ttk.Treeview(win, columns=("name", "cat", "size"), show="headings", height=14)
        for c, t, w in (("name", "Item", 250), ("cat", "Type", 170), ("size", "Size", 60)):
            lt.heading(c, text=t)
            lt.column(c, width=w, anchor="w")
        lt.pack(fill="both", expand=True, padx=10, pady=6)

        def fill(*_):
            lt.delete(*lt.get_children())
            s = q.get().lower()
            for i, r in enumerate(rows):
                if s in item_label(r).lower() or s in item_category(r[1]).lower():
                    w, h = SIZES.of(r)
                    lt.insert("", "end", iid=str(i), values=(item_label(r), item_category(r[1]), f"{w}x{h}"))
        q.trace_add("write", fill)
        fill()
        ent.focus_set()
        bf = ttk.Frame(win, padding=10)
        bf.pack(fill="x")
        ttk.Label(bf, text="Amount:").pack(side="left")
        amount = tk.IntVar(value=1)
        ttk.Spinbox(bf, from_=1, to=50, textvariable=amount, width=5).pack(side="left", padx=4)
        ttk.Label(bf, text="Put in:").pack(side="left", padx=(12, 0))
        dest = tk.StringVar(value="backpack")
        ttk.Radiobutton(bf, text="Backpack", variable=dest, value="backpack").pack(side="left")
        ttk.Radiobutton(bf, text="Caravan stash", variable=dest, value="stash").pack(side="left")

        def do_spawn():
            sel = lt.selection()
            if not sel:
                messagebox.showinfo("Spawn", "Pick an item.", parent=win)
                return
            r = rows[int(sel[0])]
            try:
                n = max(1, int(amount.get()))
            except (tk.TclError, ValueError):
                n = 1
            done = 0
            for _ in range(n):
                if not self.place(fresh_copy(r), dest.get()):
                    break
                done += 1
            if done:
                self.mark_dirty()
                self.refresh_all()
            msg = f"Spawned {done}x {item_label(r)} into the {dest.get()}."
            if done < n:
                msg += f"\nOnly {done} of {n} fit - no more free space."
            self.status.set(msg + " Remember to 'Save changes'.")
            if done < n:
                messagebox.showinfo("Spawn", msg, parent=win)

        ttk.Button(bf, text="Spawn", command=do_spawn).pack(side="right")
        lt.bind("<Double-1>", lambda e: do_spawn())

    def edit_item(self):
        it = self.selected_item()
        if it is None:
            return
        win = tk.Toplevel(self.root)
        win.title(f"Edit {item_label(it)}")
        win.geometry("520x520")
        ttk.Label(win, text="Item properties (JSON). Change values carefully.").pack(anchor="w", padx=6, pady=4)
        txt = tk.Text(win, wrap="none", font=self.F["mono"], padx=8, pady=6)
        txt.pack(fill="both", expand=True, padx=6)
        txt.insert("1.0", json.dumps(it[1], indent=2, ensure_ascii=False))

        def ok():
            try:
                new = json.loads(txt.get("1.0", "end"))
                if not isinstance(new, dict):
                    raise ValueError("must be a JSON object")
            except Exception as e:
                messagebox.showerror("Invalid JSON", str(e), parent=win)
                return
            it[1] = new
            self.mark_dirty()
            self.refresh_all()
            win.destroy()
        ttk.Button(win, text="OK", command=ok).pack(pady=6)

    def enchant_item(self):
        it = self.selected_item()
        if it is None:
            return
        p = it[1]
        pool = enchant_pool(p)
        if not pool:
            messagebox.showinfo("Enchant", "This item can't be enchanted (only weapons, armor, "
                                           "shields and jewelry can).")
            return
        if p.get("quality", 1) >= 4 or p.get("rarity") == "Unique":
            if not messagebox.askyesno("Enchant", "This is a unique/special item. Replacing its "
                                                  "enchants may be odd. Continue?"):
                return
        win = tk.Toplevel(self.root)
        win.title(f"Enchant {item_label(it)}")
        win.geometry("520x380")
        win.transient(self.root)
        ttk.Label(win, text="Current: " + (", ".join(f"{stat_name(a)} {fmt_stat(a, b)}" for a, b in current_enchants(p))
                                           or "no enchants")).pack(anchor="w", padx=12, pady=(12, 4))
        cf = ttk.Frame(win)
        cf.pack(anchor="w", padx=12)
        ttk.Label(cf, text="Number of enchants:").pack(side="left")
        count = tk.IntVar(value=max(1, min(2, len(current_enchants(p)) or 1)))
        rows_frame = ttk.Frame(win, padding=12)
        rows = []
        kinds = {1: "common", 2: "rare"}
        disp = {f"{stat_name(r[0])}  ({kinds.get(r[2], '?')})": r[0] for r in pool}
        rev = {v: k for k, v in disp.items()}
        choices = sorted(disp, key=lambda d: (d.endswith("(rare)"), d))

        def build_rows():
            for w in rows_frame.winfo_children():
                w.destroy()
            rows.clear()
            for i, (st, v) in enumerate(roll_enchants(p, count.get())):
                sv, vv = tk.StringVar(value=rev.get(st, st)), tk.StringVar(value=f"{v:g}")
                cb = ttk.Combobox(rows_frame, textvariable=sv, values=choices, state="readonly", width=34)
                cb.grid(row=i, column=0, pady=3, sticky="w")
                cb.bind("<<ComboboxSelected>>",
                        lambda e, sv=sv, vv=vv: vv.set(f"{enchant_value(disp.get(sv.get(), '')):g}"))
                ttk.Entry(rows_frame, textvariable=vv, width=8, justify="right").grid(row=i, column=1, padx=6)
                ttk.Label(rows_frame, text="value", style="Muted.TLabel").grid(row=i, column=2, sticky="w")
                rows.append((sv, vv))

        for n, lbl in ((1, "1 (Enchanted)"), (2, "2 (Magical)")):
            ttk.Radiobutton(cf, text=lbl, variable=count, value=n, command=build_rows).pack(side="left", padx=4)
        rows_frame.pack(fill="x")

        def apply():
            chosen = []
            for sv, vv in rows:
                try:
                    v = float(vv.get())
                except ValueError:
                    messagebox.showerror("Enchant", "Values must be numbers.", parent=win)
                    return
                key = disp.get(sv.get(), sv.get())
                if key in [c[0] for c in chosen]:
                    messagebox.showerror("Enchant", "Pick different stats for each enchant.", parent=win)
                    return
                chosen.append((key, v))
            apply_enchants(p, chosen)
            self.mark_dirty()
            self.refresh_all()
            self.status.set(f"Enchanted {item_label(it)}: " + ", ".join(f"{stat_name(a)} {fmt_stat(a, b)}" for a, b in chosen)
                            + ". Remember to 'Save changes'.")
            win.destroy()

        def remove():
            apply_enchants(p, [])
            self.mark_dirty()
            self.refresh_all()
            win.destroy()

        build_rows()
        meta = p.get("Metatype", "")
        ttk.Label(win, text=f"{len(pool)} enchants fit this {meta.lower() or 'item'}, with the game's own fixed values.\n"
                            "Like the game, each enchant comes 50/50 from the common or the rare pool.\n"
                            f"Rules: {ENCHANT_RULES['source']}.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", padx=12)
        bf = ttk.Frame(win, padding=10)
        bf.pack(fill="x", side="bottom")
        ttk.Button(bf, text="Re-roll", command=build_rows).pack(side="left")
        ttk.Button(bf, text="Remove enchants", command=remove).pack(side="left", padx=6)
        ttk.Button(bf, text="Apply", style="Accent.TButton", command=apply).pack(side="right")

    def set_charges(self):
        it = self.selected_item()
        if it is None:
            return
        cur = it[1].get("charge", 1)
        v = simpledialog.askfloat("Charges", "New number of charges/uses:", initialvalue=cur, parent=self.root)
        if v is None:
            return
        it[1]["charge"] = float(v)
        if isinstance(it[5], float) and it[5] >= 0:
            it[5] = float(v)
        self.mark_dirty()
        self.refresh_all()

    def delete_item(self):
        e = self.selected_entry()
        if e is None or not messagebox.askyesno("Delete", f"Delete {item_label(e[1])}?"):
            return
        e[0].remove(e[1])
        self.mark_dirty()
        self.refresh_all()

    def _for_all(self, fn, msg):
        if not self.data:
            return
        n = sum(1 for _, it, _ in self.entries if fn(it[1]))
        self.mark_dirty()
        self.refresh_all()
        self.status.set(f"{msg}: {n} item(s). Remember to 'Save changes'.")

    def fix_enchants(self):
        """Set every enchant to the game's own value (older Shardsmith versions used guessed values)."""
        if not self.data:
            return
        rules = {r[0]: float(r[3]) for r in ENCHANT_RULES["list"]}
        fixed, odd = [], []
        for _, it, _ in self.entries:
            p = it[1]
            ench = current_enchants(p)
            if not ench:
                continue
            new, changed = [], False
            for st, v in ench:
                if st in rules and abs(rules[st] - v) > 0.01 and not p.get("is_cursed"):
                    new.append((st, rules[st]))
                    changed = True
                    fixed.append(f"{item_label(it)}: {stat_name(st)} {fmt_stat(st, v)} \u2192 {fmt_stat(st, rules[st])}")
                else:
                    new.append((st, v))
                    if st not in rules:
                        odd.append(f"{item_label(it)}: {stat_name(st)} {fmt_stat(st, v)}")
            if changed:
                keep = {k: p[k] for k in ("quality", "Suffix", "key") if k in p}
                apply_enchants(p, new)
                p.update(keep)
        msg = (f"Fixed {len(fixed)} enchant(s):\n" + "\n".join(fixed)) if fixed else "All enchant values already match the game."
        if odd:
            msg += ("\n\nThese enchants are not ones the game rolls (they still count as stats):\n"
                    + "\n".join(odd) + "\nRe-enchant those items if you want only real enchants.")
        if fixed:
            self.mark_dirty()
            self.refresh_all()
            msg += "\n\nRemember to 'Save changes'."
        messagebox.showinfo("Fix enchant values", msg)

    def repair_all(self):
        def fn(p):
            if p.get("MaxDuration") and p.get("Duration", 0) < p["MaxDuration"]:
                p["Duration"] = p["MaxDuration"]
                return True
        self._for_all(fn, "Repaired")

    def identify_all(self):
        def fn(p):
            if p.get("identified") == 0.0:
                p["identified"] = 1.0
                return True
        self._for_all(fn, "Identified")

    def uncurse_all(self):
        def fn(p):
            if p.get("is_cursed") or p.get("Curse"):
                p["is_cursed"] = 0.0
                if "Curse" in p:
                    p["Curse"] = []
                return True
        self._for_all(fn, "Uncursed")

    # =============================================================== skills tab
    def build_skills_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text=" " + "Skills", image=self.ui_img["skills"], compound="left")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="Search:").pack(side="left")
        self.skill_q = tk.StringVar()
        self.skill_q.trace_add("write", lambda *_: self.refresh_skills())
        ttk.Entry(top, textvariable=self.skill_q, width=24).pack(side="left", padx=4)
        self.only_learned = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only learned", variable=self.only_learned,
                        command=self.refresh_skills).pack(side="left", padx=8)
        self.sp_lbl = ttk.Label(top, text="")
        self.sp_lbl.pack(side="right")
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        cols = ("type", "state")
        self.stree = ttk.Treeview(body, columns=cols, show="tree headings", height=18)
        self.stree.heading("#0", text="Skill")
        self.stree.column("#0", width=300)
        self.stree.heading("type", text="Type")
        self.stree.column("type", width=90)
        self.stree.heading("state", text="State")
        self.stree.column("state", width=110)
        self.stree.tag_configure("group", font=self.F["h3"], foreground=PAL["accent_hi"])
        self.stree.tag_configure("learned", foreground="#6fdc8c")
        self.stree.tag_configure("locked", foreground=PAL["faint"])
        self.stree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.stree.yview)
        sb.pack(side="left", fill="y")
        self.stree.configure(yscrollcommand=sb.set)
        btns = ttk.Frame(body, padding=(8, 0))
        btns.pack(side="left", fill="y")
        for text, cmd in (("Learn", lambda: self.skill_action("learn")),
                          ("Unlearn", lambda: self.skill_action("unlearn")),
                          (None, None),
                          ("Unlock (make learnable)", lambda: self.skill_action("unlock")),
                          ("Lock", lambda: self.skill_action("lock")),
                          (None, None),
                          ("Unlock whole tree", lambda: self.skill_action("unlock", whole_tree=True)),
                          ("Give +1 ability point", self.give_sp)):
            if text is None:
                ttk.Separator(btns).pack(fill="x", pady=6)
            else:
                ttk.Button(btns, text=text, command=cmd, width=22).pack(pady=2)
        ttk.Label(btns, text="Tip: the most 'legit' way is\nUnlock + give ability points,\n"
                             "then learn it in game.\nLearned active skills are put\non your hotbar.",
                  style="Muted.TLabel", justify="left").pack(pady=10, anchor="w")
        self.stree.bind("<Double-1>", lambda e: self.skill_action("toggle"))

    def skill_rows(self):
        s = self.data.get("skillsDataMap", {}).get("skillsAllDataList", [])
        return [(i, s[i]) for i in range(0, len(s) - 4, 5) if isinstance(s[i], str)]

    @staticmethod
    def skill_state(s, i):
        if s[i + 1] not in (0.0, False, 0):
            return "Learned"
        return "Locked" if s[i + 3] not in (0.0, False, 0) else "Available"

    def refresh_skills(self):
        if not self.data:
            return
        self.stree.delete(*self.stree.get_children())
        s = self.data["skillsDataMap"]["skillsAllDataList"]
        q = self.skill_q.get().lower()
        groups, counts = {}, {}
        keys = tuple(k for _, k in self.skill_rows())
        if not self.skill_db_cache or self.skill_db_cache[0] != keys:
            self.skill_db_cache = (keys, build_skill_db(list(keys)))
        db = self.skill_db_cache[1]
        for i, key in self.skill_rows():
            name, tree, passive = db.get(key, [key, "Other", key.startswith("o_pass")])
            state = self.skill_state(s, i)
            if self.only_learned.get() and state != "Learned":
                continue
            if q and q not in name.lower() and q not in tree.lower():
                continue
            if tree not in groups:
                groups[tree] = self.stree.insert("", "end", text=tree, open=bool(q) or self.only_learned.get(),
                                                 tags=("group",))
                counts[tree] = [0, 0]
            counts[tree][1] += 1
            counts[tree][0] += state == "Learned"
            self.stree.insert(groups[tree], "end", iid=f"sk{i}", text=name,
                              values=("Passive" if passive else "Active", state),
                              tags=(state.lower(),))
        for tree, gid in groups.items():
            self.stree.item(gid, text=f"{tree}  ({counts[tree][0]}/{counts[tree][1]} learned)")
        sp = self.data.get("characterDataMap", {}).get("SP", 0)
        self.sp_lbl.config(text=f"Unspent ability points: {sp:g}")

    def panel_add(self, key):
        obj = key[:-4] if key.endswith("_ico") else key
        panel = self.data["skillsDataMap"].get("skillsPanelDataList", [])
        if any(obj in row for row in panel):
            return
        for row in panel:
            for j, v in enumerate(row):
                if v == -4.0:
                    row[j] = obj
                    return

    def panel_remove(self, key):
        obj = key[:-4] if key.endswith("_ico") else key
        for row in self.data["skillsDataMap"].get("skillsPanelDataList", []):
            for j, v in enumerate(row):
                if v == obj:
                    row[j] = -4.0

    def skill_action(self, action, whole_tree=False):
        if not self.data:
            return
        sel = [x for x in self.stree.selection()]
        if not sel:
            messagebox.showinfo("Skills", "Select a skill (or a tree) first.")
            return
        s = self.data["skillsDataMap"]["skillsAllDataList"]
        targets = set()
        for iid in sel:
            if iid.startswith("sk"):
                if whole_tree:
                    iid = self.stree.parent(iid)
                else:
                    targets.add(int(iid[2:]))
                    continue
            targets.update(int(c[2:]) for c in self.stree.get_children(iid))
        for i in targets:
            key = s[i]
            act = action
            if act == "toggle":
                act = "unlearn" if self.skill_state(s, i) == "Learned" else "learn"
            passive = key.startswith("o_pass")
            if act == "learn":
                s[i + 1], s[i + 3] = 1.0, 0.0
                if not passive:
                    self.panel_add(key)
            elif act == "unlearn":
                s[i + 1] = 0.0
                if not passive:
                    self.panel_remove(key)
            elif act == "unlock":
                s[i + 3] = 0.0
            elif act == "lock" and self.skill_state(s, i) != "Learned":
                s[i + 3] = 1.0
        self.mark_dirty()
        self.refresh_skills()
        for iid in sel:
            if self.stree.exists(iid):
                self.stree.selection_add(iid)
                self.stree.see(iid)

    def give_sp(self):
        if not self.data:
            return
        c = self.data["characterDataMap"]
        c["SP"] = float(c.get("SP", 0)) + 1
        self.mark_dirty()
        self.refresh_skills()
        self.refresh_stats()

    # ============================================================== raw tab
    def build_raw_tab(self):
        f = ttk.Frame(self.nb, padding=16)
        self.nb.add(f, text=" " + "Advanced", image=self.ui_img["advanced"], compound="left")
        box = ttk.LabelFrame(f, text=" Raw save data ", padding=(14, 10))
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(box, text="Export the whole save as readable JSON, edit anything in a text\n"
                            "editor, then import it back. Shardsmith re-signs it when you save.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Button(row, text="Export JSON...", command=self.export_json).pack(side="left")
        ttk.Button(row, text="Import JSON...", command=self.import_json).pack(side="left", padx=8)
        box2 = ttk.LabelFrame(f, text=" Tools ", padding=(14, 10))
        box2.grid(row=0, column=1, sticky="nsew")
        ttk.Button(box2, text="Open saves folder", width=26, command=lambda: os.startfile(SAVES)
                   if hasattr(os, "startfile") else None).pack(anchor="w", pady=(0, 6))
        ttk.Button(box2, text="Open Shardsmith data folder", width=26,
                   command=lambda: os.startfile(APPDATA_DIR) if hasattr(os, "startfile")
                   and os.path.isdir(APPDATA_DIR) else None).pack(anchor="w", pady=(0, 6))
        ttk.Button(box2, text="Rescan items for Spawn list", width=26,
                   command=self.rescan_library).pack(anchor="w")
        box3 = ttk.LabelFrame(f, text=" Inventory mods ", padding=(14, 10))
        box3.grid(row=0, column=2, sticky="nsew", padx=(14, 0))
        ttk.Label(box3, text="Backpack rows", ).pack(anchor="w")
        cur = load_config().get("backpack_rows")
        self.rows_var = tk.StringVar(value=str(cur) if cur else "Auto")
        cb = ttk.Combobox(box3, textvariable=self.rows_var, state="readonly", width=10,
                          values=["Auto"] + [str(n) for n in range(5, 21)])
        cb.pack(anchor="w", pady=(4, 6))
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_backpack_rows())
        self.rows_info = ttk.Label(box3, text="", style="Muted.TLabel", justify="left", wraplength=230)
        self.rows_info.pack(anchor="w")
        self.update_rows_info()
        ttk.Label(f, text="Every save writes a backup next to the save first (data.sav.bak_<date>).",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def set_backpack_rows(self):
        cfg = load_config()
        v = self.rows_var.get()
        if v == "Auto":
            cfg.pop("backpack_rows", None)
        else:
            cfg["backpack_rows"] = int(v)
        save_config(cfg)
        self.detect_backpack_rows()
        self.update_rows_info()
        self.refresh_all()

    def update_rows_info(self):
        if not hasattr(self, "rows_info"):
            return
        seen = load_config().get("backpack_rows_seen", BACKPACK_H)
        self.rows_info.config(text=f"Normal game: 5 rows. Inventory mods can add more.\n"
                                   f"Auto uses the most rows seen in your saves ({seen}).\n"
                                   f"Now using: {self.backpack_rows} rows (10 wide).")

    def rescan_library(self):
        self.library = None
        self.status.set("Spawn list will be rebuilt from all your saves next time you open it.")

    def export_json(self):
        if not self.data:
            return
        p = filedialog.asksaveasfilename(defaultextension=".json", initialfile="stoneshard_save.json")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, ensure_ascii=False)
            self.status.set(f"Exported to {p}")

    def import_json(self):
        if not self.data:
            messagebox.showinfo("Import", "Load the save you want to replace first.")
            return
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            self.data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Import", f"Invalid JSON:\n{e}")
            return
        self.mark_dirty()
        self.refresh_all()
        self.status.set(f"Imported {p}. Press 'Save changes' to write it to {self.path}")

    # -------- common
    def refresh_all(self):
        if self.data:
            self.refresh_stats()
            self.refresh_inventory()
            self.refresh_skills()
            self.draw_equipment()
            self.draw_header()

    def mark_dirty(self):
        self.dirty = True
        self.root.title(APP_TITLE + " *")
        self.draw_header()

    def save(self):
        if not self.data:
            return
        try:
            b = backup(self.path)
            write_file(self.data, self.path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.dirty = False
        self.root.title(APP_TITLE)
        self.status.set(f"Saved. Backup: {os.path.basename(b)}")
        self.chron_char = None
        self.draw_header()
        messagebox.showinfo("Saved", f"Save written and re-signed.\nBackup: {b}")

    def show_about(self):
        win = tk.Toplevel(self.root)
        win.title("About Shardsmith")
        win.resizable(False, False)
        win.transient(self.root)
        f = ttk.Frame(win, padding=16)
        f.pack(fill="both", expand=True)
        try:
            self._about_icon = tk.PhotoImage(data=APP_ICON_PNG).zoom(3)
            ttk.Label(f, image=self._about_icon).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
        except tk.TclError:
            pass
        ttk.Label(f, text="SHARDSMITH", font=self.F["h1"], foreground="#eaf7f5").grid(row=0, column=1, sticky="w")
        ttk.Label(f, text=f"reforge your fate \u2014 a Stoneshard save editor by {APP_AUTHOR}",
                  style="Muted.TLabel").grid(row=1, column=1, sticky="nw")
        ttk.Label(f, text=f"Detected game: Stoneshard {SIZES.build_label()}", style="H3.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Label(f, text=COPYRIGHT_NOTICE, wraplength=380, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 10))
        ttk.Button(f, text="Close", command=win.destroy).grid(row=4, column=1, sticky="e")

    def on_close(self):
        if self.dirty and not messagebox.askyesno("Unsaved changes", "Quit without saving?"):
            return
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
