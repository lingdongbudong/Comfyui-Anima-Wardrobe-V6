# -*- coding: utf-8 -*-
"""v5.10.0：新增「⚔️ 武器场景」组（8 类 × 6 构图 = 48 条）。

口径（用户 2026-11-06 拍板）：
  · 与 场景搭配 / 艺术构图 同等级同一原理：独立 group、共用 /scenes 数据、
    同一套 n_canon 权重套用 / 收藏 / 画幅徽标；
  · 全部 `manual_only`（不进随机池，随机分布零漂移）、`1girl, solo`、全 L0；
  · 按构图混合画幅：竖 3:4（低机位 / 斜角定格 / 夜景）与横 16:9
    （逆光剪影 / 回身 / 烟尘广角），靠 `comp` 由 build_scene_aspects 派生；
  · 词库没有武器字段 → 武器名与构图细节走 `extra` + `scene_nl`；
  · 与 art 组一致**不内置服装**，服装交给角色预设 / 服装库 / 随机；
  · 只写持械造型与帅气光影，不写击中、伤害、血腥或第二个人。

⚠️ 纪律：默认**拒绝覆盖任何已存在 id**（v5.7.0 踩过静默覆盖丢两条场景）。
   插入前先 grep 全库 id；写盘前备份到毫秒级目录。
⚠️ `scene_nl` 句内禁逗号；`extra` 里禁 camera / lens / foreground / closest
   （会画出实体相机）；机位一律靠 `viewpoint` 字段表达。
"""
import datetime
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "tests"))
import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
SCENES = os.path.join(PLUGIN, "presets", "anima_v4_scenes.json")

# ── 6 个构图模板（每类 6 条依次使用；3 竖 / 3 横）───────────────────────
POSES = [
    dict(key="low", zh="低机位仰拍", viewpoint="from below",
         shot_size="cowboy shot", comp="portrait", pose_base="standing",
         light_fx="rim lighting", emotion="serious", mouth="closed mouth",
         eye_gaze="looking ahead", dof="deep focus",
         ex_cam="a clean vertical line running through her stance",
         ex_light="a hard rim of light traces her outline",
         nl_cam="The low viewpoint makes her stance tower over the frame.",
         nl_light="A hard rim of light traces her outline.",
         nl_empty="The space behind her stays empty and still."),
    dict(key="silhouette", zh="逆光横剪影", viewpoint="from side",
         shot_size="full body", comp="landscape", pose_base="contrapposto",
         light_fx="backlighting", emotion="expressionless", mouth="closed mouth",
         eye_gaze="looking away", dof="deep focus",
         ex_cam="a long horizontal silhouette across the frame",
         ex_light="backlight carves a sharp edge around the weapon",
         nl_cam="Wide empty space balances the long horizontal silhouette.",
         nl_light="The backlight carves a sharp edge around the weapon.",
         nl_empty="Empty ground reaches the far horizon behind her."),
    dict(key="draw", zh="斜角定格", viewpoint="dutch angle",
         shot_size="cowboy shot", comp="portrait", pose_base="legs apart",
         light_fx="god rays", emotion="serious", mouth="closed mouth",
         eye_gaze="looking at viewer", dof="focus on face",
         ex_cam="a dynamic diagonal composition with one frozen motion",
         ex_light="a single shaft of light falls on the weapon",
         nl_cam="The tilted frame turns the pose into a dynamic diagonal.",
         nl_light="A single shaft of light falls across the weapon.",
         nl_empty="The space behind her stays empty and still."),
    dict(key="turn", zh="回身横扫", viewpoint="from behind",
         shot_size="full body", comp="landscape", pose_base="contrapposto",
         light_fx="warm lighting", emotion="smug", mouth="closed mouth",
         eye_gaze="looking back", dof="deep focus",
         ex_cam="a wide arc of movement across the frame",
         ex_light="warm light rakes across the scene",
         nl_cam="A wide arc of movement crosses the frame.",
         nl_light="Warm light rakes across the scene.",
         nl_empty="Empty ground reaches the far horizon behind her."),
    dict(key="dust", zh="烟尘广角", viewpoint="from side",
         shot_size="wide shot", comp="landscape", pose_base="standing",
         light_fx="soft lighting", emotion="serious", mouth="closed mouth",
         eye_gaze="looking ahead", dof="deep focus",
         ex_cam="a vast empty ground around one small figure",
         ex_light="soft light settles evenly across the whole frame",
         nl_cam="Vast empty ground stretches to both edges of the frame.",
         nl_light="Soft light settles evenly across the whole frame.",
         nl_empty="Empty ground reaches the far horizon behind her."),
    dict(key="night", zh="霓虹夜景", viewpoint="from side",
         shot_size="full body", comp="portrait", pose_base="standing",
         light_fx="neon lights", emotion="serious", mouth="closed mouth",
         eye_gaze="looking to the side", dof="depth of field, blurry background",
         ex_cam="colored light streaks behind the subject",
         ex_light="neon light picks out the weapon edge",
         nl_cam="Colored light streaks behind her.",
         nl_light="Colored light pools beneath her feet.",
         nl_empty="The street behind her stays empty and still."),
]

# 每条：(slug, 中文名, weapon, detail, hold, place, drama, 三句正文[无逗号])
CATS = [
    ("blade", "刀剑", [
        ("iaijutsu_low", "太刀 · 低机位拔刀", "katana",
         "a polished katana with a black lacquered scabbard",
         "one hand rests on the hilt and the blade stays sheathed",
         "a stone shrine path at dawn",
         "cherry petals drift across the still blade",
         "She stands on a stone path at dawn. One hand rests on the hilt and the blade stays sheathed. Cherry petals drift across the polished steel."),
        ("tachi_silhouette", "太刀 · 逆光横剪影", "tachi",
         "a long curved tachi catching the last light",
         "both hands hold the sword low at her side",
         "an open hilltop under a low sun",
         "wind lifts a fine veil of grass around her feet",
         "An open hilltop holds the last light of day. Both hands keep the long sword low at her side. Wind lifts a fine veil of grass around her feet."),
        ("greatsword_draw", "大剑 · 斜角举剑", "greatsword",
         "an oversized greatsword with a broad straight blade",
         "both hands raise the greatsword above one shoulder",
         "a ruined stone hall with tall columns",
         "dust floats through a shaft of light",
         "Light falls through a break in the stone roof. Both hands raise the broad blade above one shoulder. Dust floats inside the shaft of light."),
        ("nodachi_turn", "野太刀 · 回身横扫", "nodachi",
         "an extra long nodachi with a plain dark hilt",
         "she turns at the waist and the long blade sweeps behind her",
         "a wide wooden bridge over still water",
         "the turning motion throws a long arc across the frame",
         "A wide wooden bridge crosses still water. She turns at the waist and the long blade sweeps behind her. The motion draws a wide arc across the frame."),
        ("rapier_dust", "细剑 · 烟尘广角", "rapier",
         "a slender rapier with a fine ring guard",
         "the rapier points down toward the ground at her side",
         "a vast dry plain under an overcast sky",
         "fine dust hangs low across the empty ground",
         "A vast dry plain reaches to the horizon. The slender blade points down toward the ground. Fine dust hangs low across the empty ground."),
        ("wakizashi_night", "胁差 · 霓虹夜雨", "wakizashi",
         "a short wakizashi with a pale blue glossy scabbard",
         "the short blade rests angled across her chest",
         "a glossy city street under colored signs",
         "colored light streaks across the glossy street",
         "A glossy city street carries colored signs. The short blade rests angled across her chest. Colored light streaks across the glossy street."),
    ]),
    ("gun", "枪械", [
        ("pistol_low", "手枪 · 低机位持枪", "handgun",
         "a matte black handgun with a clean slide",
         "the handgun hangs pointed down along her leg",
         "a concrete stairwell lit from above",
         "a hard rim of light outlines the barrel",
         "A concrete stairwell opens to light from above. The handgun hangs pointed down along her leg. A hard rim of light outlines the barrel."),
        ("rifle_silhouette", "步枪 · 逆光剪影", "rifle",
         "a long rifle with a slender barrel and sling",
         "the rifle rests across her shoulders behind her neck",
         "a ridge line under a setting sun",
         "the long barrel cuts a clean horizontal line",
         "A ridge line holds the setting sun. The rifle rests across her shoulders behind her neck. The long barrel cuts a clean horizontal line."),
        ("revolver_draw", "左轮 · 斜角定格", "revolver",
         "a steel revolver with a short cylinder",
         "one hand lifts the revolver to chest height",
         "a dusty doorway with warm interior light",
         "warm light flashes along the cylinder edge",
         "Warm light spills from a dusty doorway. One hand lifts the revolver to chest height. The light flashes along the cylinder edge."),
        ("shotgun_turn", "霰弹枪 · 回身", "shotgun",
         "a pump action shotgun with a dark walnut stock",
         "she turns and the shotgun swings across her back",
         "a harvest field at golden hour",
         "the swing leaves a wide arc through the tall grass",
         "A harvest field holds the golden hour. She turns and the shotgun swings across her back. The swing leaves a wide arc through the tall grass."),
        ("sniper_dust", "狙击枪 · 广角静立", "sniper rifle",
         "a long precision rifle with a matte scope",
         "the rifle stands upright beside her with the stock on the ground",
         "an empty rooftop over a hazy city",
         "haze softens every distant edge",
         "An empty rooftop overlooks a hazy city. The rifle stands upright beside her with its stock on the ground. Haze softens every distant edge."),
        ("smg_night", "冲锋枪 · 霓虹夜景", "submachine gun",
         "a compact submachine gun with a folded stock",
         "the compact weapon rests against her hip in one hand",
         "a glossy alley under neon signs",
         "colored light picks out the metal edges",
         "A glossy alley carries neon signs. The compact weapon rests against her hip. Colored light picks out the metal edges."),
    ]),
    ("bow", "弓箭", [
        ("yumi_low", "和弓 · 低机位", "yumi bow",
         "a tall asymmetric yumi bow of dark bamboo",
         "she holds the tall bow vertical at her side",
         "a shrine gate under a pale morning sky",
         "the bow rises past the top of the frame",
         "A shrine gate stands under a pale morning sky. She holds the tall bow vertical at her side. The bow rises past the top of the frame."),
        ("longbow_silhouette", "长弓 · 逆光", "longbow",
         "a long wooden bow with a taut string",
         "the bow spans the full width of the frame",
         "a forest edge at sunset",
         "the string draws one clean straight line",
         "A forest edge holds the last sunset light. The bow spans the full width of the frame. The string draws one clean straight line."),
        ("full_draw", "拉弓 · 斜角满月", "drawn bow",
         "a fully drawn bow with the string pulled to her cheek",
         "both arms hold the drawn string steady",
         "a stone courtyard with high walls",
         "one shaft of light crosses the arrow",
         "A stone courtyard sits between high walls. Both arms hold the drawn string steady. One shaft of light crosses the arrow."),
        ("horsebow_turn", "骑射弓 · 回身", "horse bow",
         "a short recurve bow with a wrapped grip",
         "she turns and the short bow arcs behind her shoulder",
         "a grassy steppe under a wide sky",
         "the turn sends a long arc across the open land",
         "A grassy steppe opens under a wide sky. She turns and the short bow arcs behind her shoulder. The turn sends a long arc across the open land."),
        ("crossbow_dust", "弩 · 广角", "crossbow",
         "a heavy crossbow with a carved wooden stock",
         "the crossbow rests level across both forearms",
         "a barren canyon floor",
         "dust hangs low between the rock walls",
         "A barren canyon floor lies between rock walls. The crossbow rests level across both forearms. Dust hangs low in the still air."),
        ("arrow_night", "箭矢 · 霓虹夜", "one single long arrow",
         "one single long arrow with pale fletching and a plain steel point",
         "both hands hold the one arrow upright beside her face",
         "a rooftop at night with city lights below",
         "colored light runs along the arrow shaft",
         "A rooftop holds the night above city lights. Both hands hold one single arrow upright beside her face. The arrow is the only weapon in the picture. Colored light runs along its shaft."),
    ]),
    ("pole", "长柄", [
        ("naginata_low", "薙刀 · 低机位", "naginata",
         "a naginata with a curved blade on a long shaft",
         "the long shaft rises straight beside her shoulder",
         "a temple veranda at dawn",
         "the curved blade catches a fine rim of light",
         "A temple veranda catches the first dawn light. The long shaft rises straight beside her shoulder. The curved blade holds a fine rim of light."),
        ("spear_silhouette", "长枪 · 逆光", "spear",
         "a long spear with a bright steel leaf point",
         "the spear lies horizontal across both hands",
         "a riverbank under a low sun",
         "the spear makes one long dark line",
         "A riverbank holds the low sun. The spear lies horizontal across both hands. The spear makes one long dark line."),
        ("halberd_draw", "戟 · 斜角", "halberd",
         "a halberd with an axe head and a hooked back",
         "she lifts the halberd to a steep diagonal",
         "a castle corridor with stone arches",
         "dust turns gold inside a light shaft",
         "A castle corridor runs between stone arches. She lifts the halberd to a steep diagonal. Dust turns gold inside a light shaft."),
        ("scythe_turn", "镰刀 · 回身", "war scythe",
         "a long war scythe with a broad crescent blade",
         "she turns and the crescent sweeps a wide low arc",
         "an empty wheat field at dusk",
         "the sweep leaves a clean curve through the stalks",
         "An empty wheat field holds the dusk. She turns and the crescent sweeps a wide low arc. The sweep leaves a clean curve through the stalks."),
        ("staff_dust", "长棍 · 广角", "bo staff",
         "a plain hardwood staff bound at both ends",
         "the staff rests upright with one end on the ground",
         "a wide dry riverbed",
         "fine dust circles the standing staff",
         "A wide dry riverbed stretches to both edges. The staff rests upright with one end on the ground. Fine dust circles the standing staff."),
        ("axe_night", "战斧 · 霓虹", "battle axe",
         "a double headed axe with a dark steel head",
         "the axe rests head down beside her knee",
         "a glossy underpass under colored lights",
         "colored light traces both axe edges",
         "A glossy underpass holds colored lights. The axe rests head down beside her knee. Colored light traces both axe edges."),
    ]),
    ("shield", "盾与重械", [
        ("kite_low", "鸢盾 · 低机位", "kite shield",
         "a tall kite shield with a worn steel rim",
         "the tall shield stands planted in front of her",
         "a cathedral nave with tall windows",
         "light falls in long stripes across the shield face",
         "A cathedral nave holds tall windows. The tall shield stands planted in front of her. Light falls in long stripes across the shield face."),
        ("round_silhouette", "圆盾 · 逆光", "round shield",
         "a round shield with a bright central boss",
         "she holds the round shield out to one side",
         "a cliff edge above a dark sea",
         "the round shape reads as one clean dark disc",
         "A cliff edge looks out over a dark sea. She holds the round shield out to one side. The round shape reads as one clean dark disc."),
        ("tower_draw", "塔盾 · 斜角", "tower shield",
         "a broad tower shield tall enough to cover her",
         "she braces the tower shield at a steep angle",
         "a narrow stone stair between two walls",
         "one shaft of light strikes the shield edge",
         "A narrow stone stair runs between two walls. She braces the tower shield at a steep angle. One shaft of light strikes the shield edge."),
        ("buckler_turn", "小圆盾 · 回身", "small round steel shield",
         "a small round steel shield strapped flat to her forearm",
         "she turns and the round shield swings forward on her forearm",
         "a sunlit training yard",
         "the turn draws a clean arc across the yard",
         "A sunlit training yard lies open and empty. She turns and the round steel shield swings forward on her forearm. The turn draws a clean arc across the yard."),
        ("wall_dust", "盾列 · 广角", "shield wall",
         "a row of tall shields set along the ground",
         "one shield stands upright beside her in the wide frame",
         "a dry plain under a pale sky",
         "dust settles along the long line of shields",
         "A dry plain reaches a pale horizon. One shield stands upright beside her in the wide frame. Dust settles along the long line of shields."),
        ("mirror_night", "镜盾 · 霓虹", "mirror shield",
         "a polished mirror shield with a silver face",
         "the polished shield rests against her shoulder",
         "a glass lobby at night",
         "colored light slides across the shield face",
         "A glass lobby holds the night inside. The polished shield rests against her shoulder. Colored light slides across the shield face."),
    ]),
    ("magic", "魔法能量", [
        ("staff_low", "法杖 · 低机位", "magic staff",
         "a tall staff crowned with a floating crystal",
         "the staff rises past her shoulder into the upper frame",
         "a dark hall lit only by the crystal",
         "cold light outlines her stance from below",
         "A dark hall holds only the crystal light. The staff rises past her shoulder into the upper frame. Cold light outlines her stance from below."),
        ("glyph_silhouette", "光环 · 逆光", "glowing arc circle",
         "a wide ring of plain glowing arcs and dots behind her",
         "the arc ring spans the whole horizontal frame",
         "a hilltop under an eclipsed sun",
         "the ring reads as one bright circular line",
         "A hilltop holds an eclipsed sun. A wide ring of plain glowing arcs and dots spans the frame. The ring reads as one bright circular line."),
        ("blade_draw", "光刃 · 斜角", "energy blade",
         "a blade of pale light with no metal hilt",
         "she lifts the light blade into a steep diagonal",
         "a vaulted chamber with black stone walls",
         "the blade throws a hard bright edge across the wall",
         "A vaulted chamber holds black stone walls. She lifts the light blade into a steep diagonal. The blade throws a hard bright edge across the wall."),
        ("grimoire_turn", "魔导书 · 回身", "grimoire",
         "an open grimoire with pages held by faint light",
         "she turns and the open book swings across her body",
         "a library aisle at warm dusk",
         "the turn leaves a wide arc of pale light",
         "A library aisle holds the warm dusk. She turns and the open book swings across her body. The turn leaves a wide arc of pale light."),
        ("orb_dust", "灵球 · 近景白雾", "floating magic orb",
         "a large glowing orb hovering beside her",
         "the orb hangs alone in a pale white haze close behind her",
         "a pale white haze close behind her",
         "fine ash drifts around the glowing orb",
         "A pale white haze sits close behind her. Only one figure stands in the whole frame. A large glowing orb hangs alone beside her. Fine ash drifts around the glowing orb."),
        ("sigil_night", "光纹 · 霓虹", "light sigil",
         "a long sigil of light drawn in the air",
         "the light sigil stands upright beside her",
         "a rooftop above a bright city at night",
         "colored light mixes with the pale sigil",
         "A rooftop rises above a bright night city. A long sigil of light stands upright beside her. Colored light mixes with the pale sigil."),
    ]),
    ("dual", "双持暗器", [
        ("dualblade_low", "双刀 · 低机位", "dual blades",
         "two matching short blades with dark hilts",
         "one blade rises and one blade rests low",
         "a narrow alley at first light",
         "the two blades form a clean vertical split",
         "A narrow alley holds the first light. One blade rises and one blade rests low. The two blades form a clean vertical split."),
        ("dualgun_silhouette", "双枪 · 逆光", "dual handguns",
         "two compact handguns with matte finishes",
         "both arms spread wide with one weapon in each hand",
         "an open rooftop at sunset",
         "the wide stance makes one long horizontal line",
         "An open rooftop holds the sunset. Both arms spread wide with one weapon in each hand. The wide stance makes one long horizontal line."),
        ("dagger_draw", "双匕首 · 斜角", "twin daggers",
         "a pair of fine daggers with silver guards",
         "she crosses both daggers in front of her chest",
         "a dark stone corridor",
         "one light shaft catches both crossed edges",
         "A dark stone corridor runs straight ahead. She crosses both daggers in front of her chest. One light shaft catches both crossed edges."),
        ("kunai_turn", "苦无 · 回身", "steel kunai",
         "one short steel kunai with a diamond shaped blade and a ring on the handle",
         "she turns and the short steel blade arcs behind her shoulder",
         "a moonlit courtyard",
         "the turn leaves a fine bright arc",
         "A moonlit courtyard lies quiet and empty. She turns and one short steel blade arcs behind her shoulder. The turn leaves a fine bright arc."),
        ("chain_dust", "锁链 · 广角", "chain weapon",
         "a long chain with a weighted end",
         "the chain hangs in a wide curve from her raised hand",
         "a bare training ground",
         "dust settles along the hanging chain",
         "A bare training ground stretches wide and flat. The chain hangs in a wide curve from her raised hand. Dust settles along the hanging chain."),
        ("shuriken_night", "手里剑 · 霓虹", "shuriken",
         "a single star shaped shuriken with a steel edge",
         "she holds the steel star up beside her eye",
         "a glossy street under colored signs",
         "colored light flashes on the steel points",
         "A glossy street carries colored signs. She holds the steel star up beside her eye. Colored light flashes on the steel points."),
    ]),
    ("sci", "科幻未来", [
        ("saber_low", "光剑 · 低机位", "energy saber",
         "a glowing energy saber with a short metal hilt",
         "the glowing blade points straight up past the top edge",
         "a dark hangar with a high ceiling",
         "the blade paints a cold rim along her stance",
         "A dark hangar holds a high ceiling. The glowing blade points straight up past the top edge. The blade paints a cold rim along her stance."),
        ("plasma_silhouette", "等离子枪 · 逆光", "plasma rifle",
         "a long plasma rifle with a lit cooling spine",
         "the long weapon lies horizontal across both hands",
         "a landing platform under a pale sun",
         "the lit spine draws one clean horizontal line",
         "A landing platform holds a pale sun. The long weapon lies horizontal across both hands. The lit spine draws one clean horizontal line."),
        ("mecharm_draw", "机械臂 · 斜角", "mechanical arm",
         "a powered mechanical arm with a bright joint",
         "she raises the powered arm into a steep diagonal",
         "a white laboratory corridor",
         "one hard light shaft crosses the metal joints",
         "A white laboratory corridor runs straight ahead. She raises the powered arm into a steep diagonal. One hard light shaft crosses the metal joints."),
        ("drone_turn", "无人机 · 回身", "hovering drone",
         "a small hovering drone with a single blue eye",
         "she turns and the drone swings a wide arc behind her",
         "an open deck above a cloud layer",
         "the turn leaves a wide circle of blue light",
         "An open deck sits above a cloud layer. She turns and the drone swings a wide arc behind her. The turn leaves a wide circle of blue light."),
        ("cannon_dust", "粒子炮 · 广角", "particle cannon",
         "a straight metal tube cannon with a rectangular housing and a long barrel",
         "the straight cannon rests level against her shoulder in a wide frame",
         "a flat metallic plain",
         "fine dust drifts along the long metal barrel",
         "A flat metallic plain reaches the horizon. The straight metal cannon rests level against her shoulder. Fine dust drifts along the long barrel."),
        ("cyberblade_night", "赛博刀 · 霓虹", "cyber blade",
         "a slim cyber blade with a glowing edge",
         "the slim blade rests angled across her shoulder",
         "a neon lit overpass at night",
         "colored light runs along the glowing edge",
         "A neon lit overpass holds the night. The slim blade rests angled across her shoulder. Colored light runs along the glowing edge."),
    ]),
]

# ══════════════════════════════════════════════════════════════════════
# v5.11.0：按「作品 → 武器」两级管理（用户 2026-11-06 下午）
#   8 个作品 × 4 件武器 = 32 条；原 48 条标为「通用」保留。
#   ⚠️ 作品名**只做元数据**，绝不写进 extra / scene_nl —— 模型靠武器几何
#      与色调识别（上一轮朗基努斯之枪试跑已验证）；写作品 tag 会串角色。
#   ⚠️ 中文武器名从 zh 的第一段提取，英文 tag 是 extra 的第一个标签，
#      两条都自动派生，不手写第二份，避免漂移。
# ══════════════════════════════════════════════════════════════════════
# 每条：(slug, 中文武器名, theme, 英文 weapon tag, detail, hold, place, drama, nl3)
IP_CATS = [
    ("eva", "neon_genesis_evangelion", "新世纪福音战士", "neon genesis evangelion", [
        ("longinus", "朗基努斯之枪", "长柄", "lance of longinus",
         "exactly one single gigantic red bident spear with two long parallel prongs and a black shaft wound with a red spiral pattern",
         "both hands close together grip the same one black shaft",
         "a red plain under a giant dark red moon",
         "the twin prongs of the one spear rise past the top edge of the frame",
         "A red plain stretches under a giant dark red moon. Both hands close together grip the same one black shaft. The twin prongs of the one spear rise past the top edge of the frame."),
        ("progknife", "高振动粒子刀", "刀剑", "progressive knife",
         "a short utility knife with a smooth straight glowing orange edge and a grey rectangular grip",
         "one hand holds the short knife raised beside her face",
         "a dark hangar with a high metal ceiling",
         "a fine orange glow runs along the straight blade",
         "A dark hangar holds a high metal ceiling. One hand holds the short knife raised beside her face. A fine orange glow runs along the straight blade."),
        ("atfield", "AT 力场", "盾与重械", "a.t. field",
         "a wide glowing orange hexagonal barrier standing in front of her",
         "one open palm faces the glowing hexagonal wall",
         "a flat concrete ground under a pale sky",
         "each hexagon of the barrier glows with a bright orange rim",
         "A flat concrete ground lies under a pale sky. One open palm faces the glowing hexagonal wall. Each hexagon of the barrier glows with a bright orange rim."),
        ("replica", "量产机复制枪", "长柄", "replica lance",
         "a row of tall red bident spears planted upright in the ground",
         "one red bident spear stands beside her in the wide frame",
         "a bare hill under a violet sky",
         "the long row of red spear shafts cuts a clean diagonal line",
         "A bare hill lies under a violet sky. One red bident spear stands beside her in the wide frame. The long row of red spear shafts cuts a clean diagonal line."),
    ]),
    ("gi", "genshin_impact", "原神", "genshin impact", [
        ("skyward", "天空之翼", "弓箭", "skyward harp",
         "a pale golden recurve bow with white feather shaped wings along its limbs",
         "both hands hold the golden bow level across her chest",
         "a windy cliff above a sea of clouds",
         "small pale feathers drift around the bow limbs",
         "A windy cliff rises above a sea of clouds. Both hands hold the golden bow level across her chest. Small pale feathers drift around the bow limbs."),
        ("wolfgrave", "狼的末路", "刀剑", "wolf gravestone",
         "an oversized greatsword with a broad black blade and a silver wolf head on the pommel",
         "both hands rest the broad blade point down on the ground",
         "a moonlit forest clearing",
         "cold light runs along the broad black blade",
         "A moonlit forest clearing lies quiet. Both hands rest the broad blade point down on the ground. Cold light runs along the broad black blade."),
        ("jadespear", "和璞鸢", "长柄", "primordial jade winged spear",
         "a long jade green spear with a pale gold wing shaped guard near the tip",
         "one hand holds the jade spear vertical at her side",
         "a stone terrace above a misty valley",
         "a green glow gathers around the wing shaped guard",
         "A stone terrace sits above a misty valley. One hand holds the jade spear vertical at her side. A green glow gathers around the wing shaped guard."),
        ("lostprayer", "四风原典", "魔法能量", "lost prayer to the sacred winds",
         "a thick open book with pale green pages held by a ring of drifting wind",
         "one hand lifts the open book beside her shoulder",
         "a high tower balcony in a clear blue sky",
         "a fine spiral of wind circles the open pages",
         "A high tower balcony opens to a clear blue sky. One hand lifts the open book beside her shoulder. A fine spiral of wind circles the open pages."),
    ]),
    ("fate", "fate", "Fate 系列", "fate", [
        ("excalibur", "誓约胜利之剑", "刀剑", "excalibur",
         "a broad straight sword with a golden hilt and a blade glowing pale gold",
         "both hands raise the glowing sword straight above her head",
         "a dark stone hall with tall pillars",
         "golden light spills down the length of the blade",
         "A dark stone hall holds tall pillars. Both hands raise the glowing sword straight above her head. Golden light spills down the length of the blade."),
        ("ea", "乖离剑 Ea", "刀剑", "sword of ea",
         "a short black sword with a spiral of three rotating red blade segments",
         "one hand holds the black spiral sword raised across her chest",
         "a ruined plain under a red sky",
         "the three red segments turn around the black core",
         "A ruined plain lies under a red sky. One hand holds the black spiral sword raised across her chest. The three red segments turn around the black core."),
        ("gaebolg", "刺穿死棘之枪", "长柄", "gae bolg",
         "a long red spear with a barbed leaf shaped point and a dark shaft",
         "one hand holds the red spear angled across her body",
         "a grassy hill at dusk",
         "a fine red line runs along the barbed point",
         "A grassy hill holds the dusk. One hand holds the red spear angled across her body. A fine red line runs along the barbed point."),
        ("avalon", "遥远的理想乡", "盾与重械", "avalon",
         "a small golden scabbard shaped shield with a pale glowing rim",
         "both hands hold the small golden shield close to her chest",
         "a quiet lakeside at golden hour",
         "a soft gold light fills the air around the shield",
         "A quiet lakeside holds the golden hour. Both hands hold the small golden shield close to her chest. A soft gold light fills the air around the shield."),
    ]),
    ("kny", "kimetsu_no_yaiba", "鬼灭之刃", "kimetsu no yaiba", [
        ("nichirin", "黑色日轮刀", "刀剑", "nichirin blade",
         "a black nichirin katana with a round silver guard and a dark red handle",
         "both hands grip the black blade in a low diagonal guard",
         "a bamboo grove at dawn",
         "pale morning light runs along the black blade",
         "A bamboo grove holds the pale dawn. Both hands grip the black blade in a low diagonal guard. Pale morning light runs along the black blade."),
        ("flame", "炎柱日轮刀", "刀剑", "flame nichirin blade",
         "a nichirin katana with a flame shaped orange guard and a red edged blade",
         "one hand holds the red edged blade high behind her shoulder",
         "a dark wooden corridor lit by warm lanterns",
         "warm light flashes along the red edge",
         "A dark wooden corridor holds warm lanterns. One hand holds the red edged blade high behind her shoulder. Warm light flashes along the red edge."),
        ("dualblade", "音柱双刀", "双持暗器", "dual nichirin blades",
         "two matching short nichirin blades joined by a fine chain at the hilts",
         "both hands hold one short blade in each hand",
         "a tiled rooftop under a night sky",
         "the fine chain hangs in a clean curve between the two hilts",
         "A tiled rooftop lies under a night sky. Both hands hold one short blade in each hand. The fine chain hangs in a clean curve between the two hilts."),
        ("insect", "蝴蝶忍细刀", "刀剑", "insect nichirin blade",
         "a slender nichirin blade with a pale purple tip and a butterfly shaped guard",
         "one hand holds the slender blade pointed down at her side",
         "a garden of pale purple wisteria",
         "tiny purple petals drift along the slender blade",
         "A garden holds pale purple wisteria. One hand holds the slender blade pointed down at her side. Tiny purple petals drift along the slender blade."),
    ]),
    ("nier", "nier_automata", "尼尔：机械纪元", "nier automata", [
        ("virtuous", "白之契约", "刀剑", "virtuous contract",
         "a white longsword with a plain rectangular blade and a black wrapped grip",
         "one hand rests the white sword against her shoulder",
         "a ruined concrete building overgrown with green vines",
         "white light catches the flat side of the rectangular blade",
         "A ruined concrete building holds green vines. One hand rests the white sword against her shoulder. White light catches the flat side of the rectangular blade."),
        ("bloodoath", "黑之血盟", "刀剑", "cruel blood oath",
         "a black broadsword with a jagged red edge and a heavy square guard",
         "both hands hold the black broadsword low at her side",
         "a desert of pale sand dunes at noon",
         "a fine red glow runs along the jagged edge",
         "A desert of pale sand dunes holds the noon light. Both hands hold the black broadsword low at her side. A fine red glow runs along the jagged edge."),
        ("pod", "Pod 辅助机", "科幻未来", "pod drone",
         "a small rounded black drone with a single blue eye and two fine arms",
         "the small drone hovers beside her shoulder",
         "a cliff of pale stone under a white sky",
         "a fine blue light sweeps from the hovering drone",
         "A cliff of pale stone lies under a white sky. The small drone hovers beside her shoulder. A fine blue light sweeps from the hovering drone."),
        ("machinearm", "机械臂刃", "科幻未来", "machine blade",
         "a long silver mechanical arm blade with three bright joints",
         "the silver blade arm extends level beside her",
         "a dark factory floor with pale steel beams",
         "cold light picks out the three bright joints",
         "A dark factory floor holds pale steel beams. The silver blade arm extends level beside her. Cold light picks out the three bright joints."),
    ]),
    ("hsr", "honkai_star_rail", "崩坏：星穹铁道", "honkai star rail", [
        ("lightcone", "光锥", "魔法能量", "light cone",
         "a tall glowing prism of pale blue light floating beside her",
         "one open palm faces the floating blue prism",
         "a star filled void with a pale metal platform",
         "fine blue lines of light run inside the prism",
         "A pale metal platform floats in a star filled void. One open palm faces the floating blue prism. Thin blue lines of light run inside the prism."),
        ("cloudspear", "云骑长枪", "长柄", "cloud knight spear",
         "a long silver spear with a pale blue tassel below the leaf shaped point",
         "one hand holds the silver spear vertical at her side",
         "a cloud sea above a white stone bridge",
         "the pale blue tassel lifts in a clean curve",
         "A white stone bridge crosses a sea of clouds. One hand holds the silver spear vertical at her side. The pale blue tassel lifts in a clean curve."),
        ("destruction", "毁灭巨剑", "刀剑", "destruction greatsword",
         "an oversized broken greatsword with a dark blade and a glowing red crack",
         "both hands rest the broken greatsword point down on the ground",
         "a shattered stone platform under a dark red sky",
         "a fine red glow runs along the crack in the dark blade",
         "A shattered stone platform lies under a dark red sky. Both hands rest the broken greatsword point down on the ground. A fine red glow runs along the crack in the dark blade."),
        ("erudition", "智识浮游炮", "科幻未来", "floating drone cannon",
         "a small golden ring shaped drone with a bright core floating at her side",
         "the golden ring drone hovers level beside her shoulder",
         "a wide pale hall with tall glass walls",
         "a fine gold beam of light connects the drone to her palm",
         "A wide pale hall holds tall glass walls. The golden ring drone hovers level beside her shoulder. A fine gold beam of light connects the drone to her palm."),
    ]),
    ("ff", "final_fantasy", "最终幻想", "final fantasy", [
        ("buster", "破坏剑", "刀剑", "buster sword",
         "an oversized broadsword with a wide grey blade and two round holes near the hilt",
         "both hands rest the wide blade against her shoulder",
         "a steel rooftop under a night sky with a pale moon",
         "cold moonlight runs along the wide grey blade",
         "A steel rooftop holds a pale moon. Both hands rest the wide blade against her shoulder. Cold moonlight runs along the wide grey blade."),
        ("masamune", "正宗", "刀剑", "masamune",
         "an extra long slender katana with a pale silver blade and a black wrapped handle",
         "one hand holds the long slender blade level at her side",
         "a field of pale ash under a white sky",
         "a fine white line of light runs the full length of the blade",
         "A field of pale ash lies under a white sky. One hand holds the long slender blade level at her side. A fine white line of light runs the full length of the blade."),
        ("gunblade", "枪刃", "枪械", "gunblade",
         "a long silver blade joined to a revolver style grip and a short barrel",
         "one hand holds the gunblade angled across her chest",
         "a stone balcony above a lit city at night",
         "pale light catches both the silver blade and the metal grip",
         "A stone balcony overlooks a lit night city. One hand holds the gunblade angled across her chest. Pale light catches both the silver blade and the metal grip."),
        ("sagestaff", "贤者之杖", "魔法能量", "sage staff",
         "a tall wooden staff topped with a faceted blue crystal and three gold rings",
         "both hands hold the tall staff upright in front of her",
         "a circular stone chamber with a pale blue glow",
         "soft blue light falls from the faceted crystal",
         "A circular stone chamber holds a pale blue glow. Both hands hold the tall staff upright in front of her. Soft blue light falls from the faceted crystal."),
    ]),
    ("cyber", "cyberpunk", "赛博朋克", "cyberpunk", [
        ("mantis", "螳螂刀", "双持暗器", "mantis blades",
         "two long silver blades extending from both forearms",
         "both forearms hold the long silver blades out to each side",
         "a glossy alley under colored signs",
         "colored light runs along both extended silver blades",
         "A glossy alley carries colored signs. Both forearms hold the long silver blades out to each side. Colored light runs along both extended silver blades."),
        ("smartpistol", "智能手枪", "枪械", "smart pistol",
         "a matte black smart pistol with a small blue targeting light on the slide",
         "one hand raises the black pistol up beside her face",
         "a neon lit rooftop at night",
         "a fine blue line of light glows on the slide",
         "A neon lit rooftop holds the night. One hand raises the black pistol up beside her face. A fine blue line of light glows on the slide."),
        ("monowire", "单分子线", "双持暗器", "monowire",
         "one fine glowing monowire stretched between both raised hands",
         "both raised hands hold the single fine glowing wire taut",
         "a dark concrete underpass with colored light",
         "the single wire draws one clean bright horizontal line",
         "A dark concrete underpass holds colored light. Both raised hands hold the single fine glowing wire taut. The single wire draws one clean bright horizontal line."),
        ("gorilla", "大猩猩义臂", "科幻未来", "gorilla arms",
         "two heavy silver mechanical arms fitted to her own shoulders with wide plated knuckles",
         "both heavy mechanical arms fitted to her shoulders hang low at her sides",
         "a bare concrete yard under a white noon sky",
         "hard light picks out the wide plated knuckles",
         "A bare concrete yard lies under a white noon sky. Both heavy mechanical arms fitted to her shoulders hang low at her sides. Hard light picks out the wide plated knuckles."),
    ]),
]

# 每个作品用 4 种不同构图，且固定 2 竖 + 2 横（竖 = 0/2/5，横 = 1/3/4）。
POSE_SETS = [
    (0, 1, 2, 3), (5, 4, 0, 1), (2, 3, 5, 4), (0, 4, 2, 1),
    (5, 1, 0, 3), (2, 4, 5, 1), (0, 3, 2, 4), (5, 1, 2, 4),
]

# 闭麦：随机家具道具 / 环境 / 动作 / 特效 / NSFW 细节会与持械造型打架。
# ⚠️ 与 tags 零交集（tags 里只有 9 个构图字段）。
DROPS = ["prop", "place", "weather", "action", "arms_hands", "texture_fx",
         "device", "leg", "makeup", "nails", "skin_detail", "pose_risque",
         "n_act", "n_pos", "n_fluid", "n_theme", "n_cum", "n_exposure",
         "n_fabric", "cloth_mat", "cloth_state_sfw"]

CLOTH_WORDS = ("dress", "shirt", "skirt", "uniform", "coat", "jacket",
               "kimono", "yukata", "swimsuit", "bikini", "lingerie",
               "stockings", "thighhighs", "boots", "loafers", "heels",
               "gloves", "apron", "sweater", "hoodie", "jeans", "shorts")
BAD_WORDS = ("camera", "lens", "foreground", "closest")
# 作品英文 tag 白名单，只用于校验「正文里不许出现作品名」，不写进正文。
WORK_EN = {w: en for (_s, w, _z, en, _it) in IP_CATS}


def _make_rec(sid, zh, theme, weapon_en, detail, hold, place, drama, nl3,
              T, work, work_zh):
    """一条武器场景 → 完整记录。元数据与正文**同源派生**，不手写第二份。"""
    tags = {
        "pose_base": T["pose_base"], "viewpoint": T["viewpoint"],
        "shot_size": T["shot_size"], "comp": T["comp"],
        "light_fx": T["light_fx"], "emotion": T["emotion"],
        "mouth": T["mouth"], "eye_gaze": T["eye_gaze"],
        "dof": T["dof"],
    }
    extra_parts = [weapon_en, detail, hold, T["ex_cam"], place,
                   T["ex_light"], drama,
                   "sharp focus on the weapon and the figure",
                   "one single figure in the whole frame",
                   "empty space behind the subject"]
    extra = ", ".join(extra_parts)
    # ⚠️ 全量出图复核发现 29B 广角/霓虹街景会在远景补一个路人 →
    #    补两句**正向**唯一性（不写否定句），把「只有一个人」说死。
    scene_nl = " ".join([nl3, T["nl_cam"], T["nl_light"],
                         "She is the only person in the picture.",
                         T["nl_empty"]])
    return {
        "id": sid, "zh": zh, "group": "weapon", "theme": theme,
        # v5.11.0：作品 → 武器 两级元数据。作品名**只到这里为止**，
        # 绝不写进 extra / scene_nl（见 IP_CATS 上方说明）。
        "work": work, "work_zh": work_zh,
        "weapon": zh.split(" · ")[0], "weapon_en": weapon_en,
        "desc": "%s / %s / %s：%s" % (work_zh, theme, T["zh"], zh),
        "tags": tags, "extra": extra, "scene_nl": scene_nl,
        "drop_fields": list(DROPS),
        "count_lock": "1girl, solo", "manual_only": True,
        "level": 0,
    }


def _fix_ip_landscape(rec):
    """v5.11.0 40 张全量出图复核：29B 在「横幅 + 空旷地平线」里补第二个小人影
    （25 cyber_gorilla / 29 fate_avalon / 35 hsr_cloudspear 实拍），正向唯一性句
    压不住 —— 与 v5.10.0 orb_dust 同一个坑。这里只对**新增 IP 横幅**从取景根上收：
      · wide shot → cowboy shot（人更大、远景更少；长武器仍完整可见）
      · 三句远景模板 + extra 两个短语换成贴身白雾，不给模型留空地放第二个人
    ⚠️ 通用 48 条上一轮已全量验收、本轮承诺正文一字不动，绝不进这个函数。
    """
    if rec["tags"].get("comp") != "landscape":
        return rec
    # ⚠️ 第二轮 16 张复跑：这两条原本（wide/full body + 原始远景句）验收是干净的，
    #    换成「贴身白雾」后 29B 反而在背景补了一张半透明大人脸（04 / 15 实拍），
    #    比远景小人更破坏画面。雾的「半透明」暗示是诱因 → 恢复首轮原文案。
    if rec["id"] in ("sc_wp_eva_replica", "sc_wp_nier_bloodoath"):
        return rec
    if rec["tags"].get("shot_size") == "wide shot":
        rec["tags"]["shot_size"] = "cowboy shot"
    repl = (
        ("Empty ground reaches the far horizon behind her.",
         "A low pale haze closes the space behind her."),
        ("Vast empty ground stretches to both edges of the frame.",
         "Pale haze fills both edges of the frame."),
        ("Wide empty space balances the long horizontal silhouette.",
         "Pale haze balances the long horizontal silhouette."),
        ("a vast empty ground around one small figure",
         "pale haze filling both edges of the frame"),
        ("empty space behind the subject",
         "close pale haze behind the subject"),
    )
    for a, b in repl:
        rec["scene_nl"] = rec["scene_nl"].replace(a, b)
        rec["extra"] = rec["extra"].replace(a, b)
    return rec


def build_items():
    out = []
    # ① 原 48 条「通用」武器：extra / scene_nl / tags 一字不动，只补元数据
    for cid, czh, items in CATS:
        assert len(items) == 6, (cid, len(items))
        for i, (slug, zh, weapon, detail, hold, place, drama, nl3) in enumerate(items):
            rec = _make_rec("sc_wp_%s_%s" % (cid, slug), zh, czh, weapon,
                            detail, hold, place, drama, nl3, POSES[i],
                            "general", "通用")
            # ⚠️ 上一轮五轮真机复核留下的取景修复，必须原样保留
            if cid == "magic" and slug == "orb_dust":
                rec["tags"]["shot_size"] = "upper body"
                rec["extra"] = rec["extra"].replace(
                    "a vast empty ground around one small figure",
                    "pale white haze filling both edges of the frame")
                rec["scene_nl"] = (rec["scene_nl"]
                                   .replace("Vast empty ground stretches to both edges of the frame.",
                                            "Pale haze fills both edges of the frame.")
                                   .replace("Empty ground reaches the far horizon behind her.",
                                            "A low white haze closes the space behind her."))
            out.append(rec)
    # ② 新增 32 条：8 作品 × 4 武器，构图由 POSE_SETS 分配（2 竖 + 2 横 / 作品）
    assert len(IP_CATS) == 8 and len(POSE_SETS) == 8
    for (short, work, work_zh, _work_en, items), pose_set in zip(IP_CATS, POSE_SETS):
        assert len(items) == 4, (work, len(items))
        for j, (slug, wpn_zh, theme, weapon_en, detail, hold, place,
                drama, nl3) in enumerate(items):
            T = POSES[pose_set[j]]
            zh = "%s · %s" % (wpn_zh, T["zh"])
            rec = _make_rec("sc_wp_%s_%s" % (short, slug), zh, theme,
                            weapon_en, detail, hold, place, drama, nl3,
                            T, work, work_zh)
            out.append(_fix_ip_landscape(rec))
    return out


def verify(items, existing_ids):
    errs = []
    seen = set()
    drop_keys = set(DROPS)
    for s in items:
        sid = s["id"]
        if sid in existing_ids:
            errs.append("%s: id 已存在，拒绝覆盖" % sid)
        if sid in seen:
            errs.append("%s: 本批内 id 重复" % sid)
        seen.add(sid)
        tags = s["tags"]
        if set(tags) & drop_keys:
            errs.append("%s: tags 与 drop_fields 有交集 %s"
                        % (sid, sorted(set(tags) & drop_keys)))
        for k, v in tags.items():
            _sec, fld = R.field_by_key(k)
            if not fld:
                errs.append("%s: 字段 %s 不存在" % (sid, k))
            elif v not in [o[1] for o in fld.get("opts", [])]:
                errs.append("%s: %s=%r 不是合法选项" % (sid, k, v))
        for k in DROPS:
            _sec, fld = R.field_by_key(k)
            if not fld:
                errs.append("%s: drop 字段 %s 不存在" % (sid, k))
        parts = [t.strip().lower() for t in s["extra"].split(",") if t.strip()]
        if len(parts) != len(set(parts)):
            errs.append("%s: extra 有重复标签" % sid)
        for t in parts:
            if R.tag_level(t) != 0:
                errs.append("%s: extra 标签抬档 %r -> L%s"
                            % (sid, t, R.tag_level(t)))
            if any(w in t.split() for w in CLOTH_WORDS):
                errs.append("%s: extra 含服装词 %r" % (sid, t))
            if any(w in t for w in BAD_WORDS):
                errs.append("%s: extra 含镜头词 %r" % (sid, t))
        if "," in s["scene_nl"]:
            errs.append("%s: scene_nl 句内有逗号" % sid)
        if any(w in s["scene_nl"].lower() for w in BAD_WORDS):
            errs.append("%s: scene_nl 含镜头词" % sid)
        if s["count_lock"] != "1girl, solo" or s["manual_only"] is not True:
            errs.append("%s: 人数锁 / manual_only 不对" % sid)
        # ── v5.11.0：作品 → 武器 两级元数据自洽 ──────────────────────
        for k in ("work", "work_zh", "weapon", "weapon_en"):
            if not str(s.get(k) or "").strip():
                errs.append("%s: 缺元数据 %s" % (sid, k))
        if s.get("weapon") != s["zh"].split(" · ")[0]:
            errs.append("%s: weapon 与 zh 首段不一致 %r"
                        % (sid, s.get("weapon")))
        first_tag = str(s["extra"]).split(",")[0].strip()
        if first_tag != s.get("weapon_en"):
            errs.append("%s: weapon_en 与 extra 首标签不一致 %r / %r"
                        % (sid, s.get("weapon_en"), first_tag))
        w_en = WORK_EN.get(s.get("work"), "")
        if w_en and re.search(r"\b%s\b" % re.escape(w_en),
                              (s["extra"] + " " + s["scene_nl"]).lower()):
            errs.append("%s: 作品名混进正文 %r" % (sid, w_en))
    return errs


def main():
    # v5.11.0 大改用 `--rebuild`：显式移除旧 weapon 组再写入 80 条。
    # ⚠️ 纪律：默认仍拒绝覆盖已存在 id；只有重建模式才删，且**先断言**
    #    旧组 id 恰好等于本脚本自己的 id 集合（v5.11.1 起按 build_items()
    #    动态算，48 条时代与 80 条时代都能重建）→ 混入任何手工条目即中止，
    #    绝不误删别的组。
    rebuild = "--rebuild" in sys.argv[1:]
    with open(SCENES, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if rebuild:
        old = [s for s in data["scenes"] if s.get("group") == "weapon"]
        expected = {s["id"] for s in build_items()}
        if {s["id"] for s in old} != expected:
            print("!! 重建中止：现有 weapon 组 id 与本脚本不一致（%d 条 / %d 个匹配）"
                  % (len(old), len({s["id"] for s in old} & expected)))
            return 1
        data["scenes"] = [s for s in data["scenes"] if s.get("group") != "weapon"]
        print("重建模式：移除旧 weapon 组 %d 条，其它 %d 条一字不动"
              % (len(old), len(data["scenes"])))
    existing = {s["id"] for s in data.get("scenes", [])}
    items = build_items()
    errs = verify(items, existing)
    if errs:
        print("校验失败（未写盘）：")
        for e in errs[:40]:
            print("  !!", e)
        return 1
    works = sorted({s["work"] for s in items})
    print("校验通过：%d 条 / %d 个作品 / %d 个武器类型，全部 L0 候选、零覆盖风险"
          % (len(items), len(works), len({s["theme"] for s in items})))

    wdesc = ("摄影人物持械造型，按**作品 → 武器**两级管理：通用 48 条 + "
             "新世纪福音战士 / 原神 / Fate / 鬼灭之刃 / 尼尔 / 崩坏星穹铁道 / "
             "最终幻想 / 赛博朋克 各 4 条，共 80 条。卡片标题带《作品名》，"
             "每条都标出具体武器；作品名只做元数据，不写进提示词正文。"
             "全部 1girl solo、全 L0、按构图混合竖 3:4 与横 16:9；"
             "不内置服装（交给角色服装预设与服装库），只在手动套用时可用。")
    grp = next((g for g in data.get("groups", []) if g.get("id") == "weapon"), None)
    if grp:
        grp["title"] = "⚔️ 武器场景"
        grp["desc"] = wdesc
        print("已更新 weapon 组描述")
    else:
        data["groups"].append({"id": "weapon", "title": "⚔️ 武器场景",
                               "desc": wdesc})

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    bak = os.path.join(HERE, "bak_weapon_%s" % ts)
    os.makedirs(bak, exist_ok=True)
    shutil.copy(SCENES, os.path.join(bak, "anima_v4_scenes.json"))
    print("备份:", bak)

    data["scenes"].extend(items)
    tmp = SCENES + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, SCENES)
    print("已写入 %d 条武器场景（通用 48 + IP 32），场景总数 %d -> %d"
          % (len(items), len(existing), len(data["scenes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
