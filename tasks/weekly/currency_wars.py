from utils.game_options import game_option_enabled
from module.screen import screen
from module.automation import auto
from module.config import cfg
from module.logger import log
from module.notification.notification import NotificationLevel
from tasks.base.base import Base
from tasks.base.list_drag import drag_list
from utils.date import Date
from tasks.weekly.weekly_reward import wait_for_weekly_reward
from typing import Literal, Tuple, Optional
import numpy as np
import time
import os
import re
import json


class CurrencyWarsCharacter:
    def __init__(self, name: Optional[str], pos: Literal["forward", "backward", "all", None] = "None", level: int = 1, money: int = 1):
        self.name = name  # 角色名称
        self.pos = pos  # 角色站位
        self.level = level  # 角色等级
        self.money = money  # 角色投资金额


# 入画奇航「羁绊链路」：成功通关时羁绊列表里激活到这些层数各给 20 星琼（不含投资大师限定）
BOND_LINK_TARGETS = {
    # 阵营（千星商会按三星人数算，不在这里）
    "幻月游戏": [5, 7], "奇兽花园": [5, 7], "仙舟舰队": [4, 6], "开拓银轨": [4, 6], "云上五骁": [3, 5],
    "星核猎手": [3, 5], "黄金大饭店": [3, 4], "梦境交织": [2, 4], "星间旅人": [4, 6], "小小巨人": [3, 5], "少女乐队": [4],
    # 流派
    "连击": [4, 6], "追击": [5, 7], "持续伤害": [5, 7], "群攻": [4, 6], "击破": [4, 6], "欢愉": [5, 6],
    "反击": [2, 4], "召唤": [2, 4], "能量": [2, 4], "治疗": [4, 6], "守护": [3],
}
CW_CHAR_DB_PATH = "./settings/cw_characters.json"  # 角色库：名字 → 费用/站位/羁绊，跨局累积
CW_BOND_DONE_PATH = "./settings/cw_bond_done.json"  # 已领的羁绊链路 {羁绊: [层数,...]}
CW_BOND_REACHED_PATH = "./settings/cw_bond_reached.json"  # 通关时游戏内羁绊列表已达成、还没去领的 {羁绊: [层数,...]}，不再追
CW_EXPECT_DONE_PATH = "./settings/cw_expect_done.json"  # 已完成的预期收益小目标，如 ["aura5"]
CW_PROTAGONIST_PATH = "./settings/cw_protagonist_picked.json"  # 选过主角光环的角色（预期收益：15 名不同角色）


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.debug(f"保存 {path} 失败: {e}")


class CurrencyWars:
    def __init__(self):
        self._last_battle_keepalive: Optional[float] = None
        self.screenshot = None  # 任务截图
        self.peipei_count: int = 0  # 佩佩和叽米
        self.diamond_count: int = 0  # 财富宝钻
        self.current_level: int = 0  # 当前可部署角色等级
        self.current_stage: str = "0-0"  # 当前关卡阶段
        self.result: Optional[bool] = None  # 对局结果
        self.need_exit: bool = False  # 是否需要退出
        self.need_restart: bool = False  # 是否重开
        self._stage_unchanged_count: int = 0  # 连续未发生阶段变化计数
        self._unrecognized_stage_count: int = 0  # 连续未识别阶段计数
        self.forward_characters: list[CurrencyWarsCharacter] = []  # 存储前台角色
        self.backward_characters: list[CurrencyWarsCharacter] = []  # 存储后台角色
        self.prepare_characters: list[CurrencyWarsCharacter] = []  # 存储备战席角色
        self.character_flags: dict[str, bool] = {}
        self.character_purchase_costs = {
            "阿格莱雅": 1,
            "藿藿": 1,
            "风堇": 2,
            "缇宝": 2,
            "花火": 2,
            "佩拉": 2,
            "希儿": 3,
            "星期日": 3,
            "刻律德菈": 3,
            "符玄": 4,
            "银狼": 4,
            "瓦尔特": 5,
            "昔涟": 5,
        }
        self.remembrance_trailblazer_purchase_cost: int = 4
        self.has_aglaea_three_star: bool = False  # 阿格莱雅三星
        self.allow_buy_experience: bool = False  # 是否允许购买经验
        self.allow_equip_weapons: bool = False  # 是否允许装备武器
        self.propeller_count: int = 0  # 螺旋桨数量
        self.shoe_count: int = 0  # 鞋子数量
        self.has_seele: bool = False  # 是否拥有希儿
        self.has_seele_three_star: bool = False  # 希儿三星
        self.seele_firestorm_count: int = 0  # 希儿已装备的火力风暴潮数量
        self.seele_chainsaw_count: int = 0  # 希儿已装备的高周波电锯数量
        self.allow_seele_equip_weapons: bool = False  # 希儿策略是否允许装备武器
        self.seele_missing_basic_equips: set = set()  # 希儿策略：合成时发现缺失的初级装备
        self.zone_name_localization = {
            "forward": "前台",
            "backward": "后台",
            "prepare": "备战席"
        }
        self.pos_name_localization = {
            "forward": "前台",
            "backward": "后台",
            "all": "前后台"
        }
        self.character_info_cache: dict[str, CurrencyWarsCharacter] = {}  # 角色信息缓存,key为角色名

        self.forward_pos = [
            (684.0 / 1920, 324.0 / 1080, 123.0 / 1920, 145.0 / 1080),
            (825.0 / 1920, 328.0 / 1080, 125.0 / 1920, 139.0 / 1080),
            (967.0 / 1920, 326.0 / 1080, 127.0 / 1920, 142.0 / 1080),
            (1109.0 / 1920, 327.0 / 1080, 129.0 / 1920, 140.0 / 1080)
        ]

        self.backward_pos_6 = [
            (543.0 / 1920, 594.0 / 1080, 124.0 / 1920, 148.0 / 1080),
            (686.0 / 1920, 594.0 / 1080, 125.0 / 1920, 147.0 / 1080),
            (824.0 / 1920, 598.0 / 1080, 127.0 / 1920, 141.0 / 1080),
            (967.0 / 1920, 598.0 / 1080, 128.0 / 1920, 141.0 / 1080),
            (1105.0 / 1920, 599.0 / 1080, 134.0 / 1920, 140.0 / 1080),
            (1246.0 / 1920, 598.0 / 1080, 139.0 / 1920, 140.0 / 1080)
        ]
        self.backward_pos_7 = [
            (482.0 / 1920, 601.0 / 1080, 109.0 / 1920, 135.0 / 1080),
            (619.0 / 1920, 600.0 / 1080, 116.0 / 1920, 137.0 / 1080),
            (759.0 / 1920, 601.0 / 1080, 120.0 / 1920, 137.0 / 1080),
            (896.0 / 1920, 600.0 / 1080, 126.0 / 1920, 138.0 / 1080),
            (1037.0 / 1920, 601.0 / 1080, 131.0 / 1920, 138.0 / 1080),
            (1175.0 / 1920, 600.0 / 1080, 137.0 / 1920, 138.0 / 1080),
            (1314.0 / 1920, 600.0 / 1080, 138.0 / 1920, 134.0 / 1080)
        ]
        self.backward_pos_8 = [
            (400.0 / 1920, 596.0 / 1080, 121.0 / 1920, 140.0 / 1080),
            (549.0 / 1920, 600.0 / 1080, 114.0 / 1920, 136.0 / 1080),
            (690.0 / 1920, 601.0 / 1080, 115.0 / 1920, 133.0 / 1080),
            (830.0 / 1920, 601.0 / 1080, 118.0 / 1920, 133.0 / 1080),
            (972.0 / 1920, 602.0 / 1080, 120.0 / 1920, 133.0 / 1080),
            (1107.0 / 1920, 601.0 / 1080, 130.0 / 1920, 136.0 / 1080),
            (1244.0 / 1920, 600.0 / 1080, 142.0 / 1920, 138.0 / 1080),
            (1389.0 / 1920, 601.0 / 1080, 135.0 / 1920, 135.0 / 1080)
        ]
        self.backward_pos_9 = [
            (347.0 / 1920, 605.0 / 1080, 94.0 / 1920, 126.0 / 1080),
            (485.0 / 1920, 605.0 / 1080, 103.0 / 1920, 128.0 / 1080),
            (625.0 / 1920, 606.0 / 1080, 103.0 / 1920, 124.0 / 1080),
            (763.0 / 1920, 606.0 / 1080, 111.0 / 1920, 126.0 / 1080),
            (903.0 / 1920, 607.0 / 1080, 116.0 / 1920, 124.0 / 1080),
            (1038.0 / 1920, 600.0 / 1080, 131.0 / 1920, 139.0 / 1080),
            (1178.0 / 1920, 604.0 / 1080, 132.0 / 1920, 131.0 / 1080),
            (1318.0 / 1920, 602.0 / 1080, 135.0 / 1920, 131.0 / 1080),
            (1455.0 / 1920, 599.0 / 1080, 146.0 / 1920, 137.0 / 1080)
        ]
        self.backward_pos = None

        self.prepare_pos = [
            (382.0 / 1920, 846.0 / 1080, 115.0 / 1920, 134.0 / 1080),
            (508.0 / 1920, 844.0 / 1080, 113.0 / 1920, 137.0 / 1080),
            (631.0 / 1920, 845.0 / 1080, 117.0 / 1920, 137.0 / 1080),
            (755.0 / 1920, 844.0 / 1080, 119.0 / 1920, 137.0 / 1080),
            (880.0 / 1920, 844.0 / 1080, 116.0 / 1920, 136.0 / 1080),
            (1004.0 / 1920, 844.0 / 1080, 118.0 / 1920, 137.0 / 1080),
            (1129.0 / 1920, 843.0 / 1080, 119.0 / 1920, 135.0 / 1080),
            (1254.0 / 1920, 842.0 / 1080, 118.0 / 1920, 138.0 / 1080),
            (1377.0 / 1920, 842.0 / 1080, 119.0 / 1920, 138.0 / 1080)
        ]

    def get_remembrance_trailblazer_name(self) -> Optional[str]:
        name = cfg.get_value("currencywars_remembrance_trailblazer_name", "")
        if isinstance(name, str):
            name = name.strip()
        return name or None

    def reset_tracked_characters(self):
        self.character_flags.clear()

    def _normalize_tracked_character_name(self, name: Optional[str]) -> Optional[str]:
        if not isinstance(name, str):
            return None
        name = name.strip()
        return name or None

    def mark_tracked_character(self, name: Optional[str], zone_name: Optional[str] = None) -> bool:
        normalized_name = self._normalize_tracked_character_name(name)
        if not normalized_name:
            return False
        if not self.character_flags.get(normalized_name, False):
            if zone_name:
                log.info(f"检测到{zone_name}存在{normalized_name}")
            else:
                log.info(f"检测到存在{normalized_name}")
        self.character_flags[normalized_name] = True
        return True

    def mark_tracked_characters(self, characters: list[CurrencyWarsCharacter], zone_name: Optional[str] = None):
        for char in characters:
            self.mark_tracked_character(char.name, zone_name)

    def has_tracked_character(self, name: Optional[str]) -> bool:
        normalized_name = self._normalize_tracked_character_name(name)
        if not normalized_name:
            return False
        return self.character_flags.get(normalized_name, False)

    def get_character_purchase_cost(self, name: Optional[str]) -> Optional[int]:
        normalized_name = self._normalize_tracked_character_name(name)
        if not normalized_name:
            return None
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        if remembrance_trailblazer_name and normalized_name == remembrance_trailblazer_name:
            return self.remembrance_trailblazer_purchase_cost
        return self.character_purchase_costs.get(normalized_name)

    def can_afford_character(self, money: int, name: Optional[str]) -> bool:
        cost = self.get_character_purchase_cost(name)
        return cost is not None and money >= cost

    def spend_character_purchase_cost(self, money: int, name: Optional[str]) -> int:
        cost = self.get_character_purchase_cost(name)
        if cost is None:
            return money
        return money - cost

    def try_buy_character(self, name: Optional[str], wait_time: float = 1.0) -> bool:
        normalized_name = self._normalize_tracked_character_name(name)
        if not normalized_name:
            return False
        if auto.click_element(normalized_name, "text", take_screenshot=False, need_ocr=False):
            log.info(f"尝试购买{normalized_name}")
            self.mark_tracked_character(normalized_name)
            time.sleep(wait_time)
            return True
        return False

    def find_deployed_character_position(self, target_name: str):
        character_groups = (
            (self.forward_characters, self.forward_pos, "前台"),
            (self.backward_characters, self.backward_pos, "后台"),
        )
        for characters, positions, zone_name in character_groups:
            if not positions:
                continue
            for index, char in enumerate(characters):
                if char.name != target_name:
                    continue
                position = auto.find_element(positions[index], "crop")
                if position:
                    log.debug(f"在{zone_name}第{index + 1}格找到{target_name}")
                return position
        log.warning(f"未能在前台或后台找到{target_name}")
        return None

    def _get_ocr_texts_in_crop(self, crop: Tuple[float, float, float, float]) -> list[str]:
        auto.take_screenshot(crop)
        auto.perform_ocr()
        return [text for _, (text, _) in auto.ocr_result]

    def _find_text_in_texts(self, texts: list[str], targets, include: bool = False) -> Optional[str]:
        target_texts = [targets] if isinstance(targets, str) else list(targets)
        for text in texts:
            for target in target_texts:
                if (include and target in text) or (not include and text == target):
                    return target
        return None

    def start(self) -> bool:
        log.hr('准备货币战争', '0')
        success = self.run()
        if success:
            Base.send_notification_with_screenshot("货币战争已完成", NotificationLevel.ALL, self.screenshot)
            self.screenshot = None
        else:
            Base.send_notification_with_screenshot("货币战争未完成", NotificationLevel.ERROR, self.screenshot)
            self.screenshot = None
        has_reward = self.get_reward()
        if Date.is_next_mon_x_am(cfg.currencywars_timestamp, cfg.refresh_hour):
            self.check_currency_wars_score()
        if has_reward and cfg.currencywars_bonus_enable:
            self.process_ornament()
        log.hr("完成", 2)
        return success

    def check_currency_wars_score(self) -> bool:
        """
        检查货币战争积分，达到 18000 时记录时间戳
        """
        screen.wait_for_screen_change("currency_wars_homepage")
        score_pos = (217.0 / 1920, 977.0 / 1080, 196.0 / 1920, 40.0 / 1080)
        score = auto.get_single_line_text(score_pos)
        if not score:
            log.warning("未识别到货币战争积分")
            return False

        score_parts = score.split('/')
        if len(score_parts) == 2 and score_parts[0].isdigit() and score_parts[1].isdigit() and score_parts[1] == "18000":
            log.info(f"货币战争积分：{score_parts[0]} / {score_parts[1]}")
            if score_parts[0] == "18000":
                cfg.save_timestamp("currencywars_timestamp")
                log.info("已达到最高积分 18000，记录时间")
                return True
        else:
            log.warning(f"无法解析货币战争积分: {score}")
        return False

    def run(self) -> bool:
        while True:
            if self.start_war(cfg.currencywars_type):
                result = self.loop()
                if self.need_restart:
                    continue
                else:
                    return result
            return False

    def get_reward(self):
        log.info("开始领取奖励")
        if auto.find_element("./assets/images/share/base/RedExclamationMark.png", "image", 0.9, crop=(28.0 / 1920, 880.0 / 1080, 435.0 / 1920, 175.0 / 1080)):
            if auto.click_element("积分", 'text', crop=(28.0 / 1920, 880.0 / 1080, 435.0 / 1920, 175.0 / 1080), include=True):
                if auto.click_element("./assets/images/zh_CN/universe/one_key_receive.png", "image", 0.9, max_retries=10):
                    if wait_for_weekly_reward():
                        time.sleep(2)
                        Base.send_notification_with_screenshot("货币战争奖励已领取", NotificationLevel.ALL)
                        auto.click_element("./assets/images/zh_CN/base/click_close.png", "image", 0.8, max_retries=10)
                        time.sleep(1)
                        auto.press_key("esc")
                        return True
        return False

    def process_ornament(self):
        instance_name = cfg.instance_names["饰品提取"]
        # 使用培养目标的副本配置（如果启用）
        try:
            if cfg.build_target_enable:
                from tasks.daily.buildtarget import BuildTarget
                target_instances = BuildTarget.get_target_instances()

                for target_type, target_name in target_instances:
                    # 精确匹配副本类型
                    if target_type == "饰品提取":
                        log.info(f"使用培养目标副本: {target_name}")
                        instance_name = target_name
                        break
        except Exception as e:
            log.error(f"获取培养目标副本失败: {e}")

        log.hr(f"开始位面饰品快速提取 - {instance_name}", 2)
        screen.change_to("guide3")

        instance_type_crop = (262.0 / 1920, 289.0 / 1080, 422.0 / 1920, 624.0 / 1080)
        if not auto.click_element("饰品提取", "text", crop=instance_type_crop):
            log.info("前往饰品提取失败，结束任务")
            return False
        time.sleep(1)

        extract_crop = (691 / 1920, 285 / 1080, 970 / 1920, 139 / 1080)
        if not auto.click_element("前往提取", "text", crop=extract_crop):
            log.info("前往饰品提取失败，结束任务")
            return False
        time.sleep(1)

        instance_name_crop = (616 / 1920, 418 / 1080, 889 / 1920, 416 / 1080)
        has_found = False
        max_drags = 5
        for page in range(max_drags + 1):
            if auto.click_element(("提取"), "min_distance_text", crop=instance_name_crop, include=True, source=instance_name, source_type="text", position="right"):
                has_found = True
                break
            if page == max_drags:
                break
            if not drag_list(auto, (1100 / 1920, 770 / 1080), (1100 / 1920, 470 / 1080), crop=instance_name_crop, duration=1.0):
                return False
            # 等待界面完全停止
            time.sleep(1)
        if not has_found:
            log.info("未找到指定副本，结束任务")
            auto.press_key("esc")
            return False

        result = auto.find_element("./assets/images/share/power/trailblaze_power/button.png", "image", 0.9, max_retries=10)
        if result:
            auto.click_element_with_pos(result, action="down")
            time.sleep(0.5)
            result = auto.find_element("./assets/images/share/power/trailblaze_power/plus.png", "image", 0.9)
            if result:
                auto.click_element_with_pos(result, action="move")
                time.sleep(0.5)
                auto.mouse_up()
                if auto.click_element("./assets/images/zh_CN/base/confirm.png", "image", 0.9, max_retries=10):
                    log.info("饰品提取完成，返回主界面")
                    time.sleep(1)
                    auto.press_key("esc")
                    time.sleep(1)
                    auto.press_key("esc")
                    if screen.check_screen("guide3"):
                        return True
            else:
                auto.mouse_up()
        else:
            auto.mouse_up()

        log.info("饰品提取失败，返回主界面")
        time.sleep(1)
        auto.press_key("esc")
        time.sleep(1)
        auto.press_key("esc")
        return False

    def start_war(self, type: Literal["normal", "overclock"] = "normal") -> bool:
        log.info("开始「货币战争」")
        screen.change_to("currency_wars_mode_select")

        if auto.click_element("结束并结算", "text", crop=(1253 / 1920, 915 / 1080, 271 / 1920, 95 / 1080)):
            log.warning("检测到未结算的对局，放弃并结算中")
            if auto.click_element("放弃并结算", "text", max_retries=10, crop=(761 / 1920, 697 / 1080, 399 / 1920, 90 / 1080)):
                self.loop()

        if type == "normal":
            log.info("选择标准博弈")
            screen.change_to("currency_wars_mode_select_normal")
        else:
            log.info("选择超频博弈")
            screen.change_to("currency_wars_mode_select_overclock")

        if not self.choose_level(1):
            log.error("选择关卡失败，结束任务")
            return False

        if not self.wait_for_war_start():
            # 启动状态不明确时停止外层循环，避免继续领奖或反复创建对局。
            raise RuntimeError("货币战争启动失败：未确认进入对局")

        log.info("开始对局")
        self._aura_char = None
        self._aura5_done = False
        self._aura5_tried = 0
        self._focus_short = False
        self._last_bond_list = {}
        self._box_fails = 0
        self._bond_env = None
        self._equip_given = {}
        self._bond_focus = None
        return True

    def wait_for_war_start(self) -> bool:
        """有限重试开始按钮，并确认进入开局界面后才交给任务主循环。"""
        attempts = 0
        last_click = -3
        for poll in range(30):
            # 每轮重新识别位置，加载中或 OCR 暂时漏检时不复用旧坐标。
            pos = auto.find_element('开始对局', 'text')
            if pos:
                if attempts < 3 and poll - last_click >= 3:
                    attempts += 1
                    last_click = poll
                    log.info(f"尝试开始对局（{attempts}/3）")
                    auto.click_element_with_pos(pos)
            elif auto.find_element(('下一步', '投资环境', '请选择投资策略', '遭遇节点'), 'text') or auto.find_element(
                "./assets/images/screen/currency_wars/exit.png", "image", 0.9,
                crop=(3.0 / 1920, 37.0 / 1080, 104.0 / 1920, 57.0 / 1080)
            ):
                return True
            time.sleep(2)
        log.error("等待货币战争开局超时，未确认进入对局")
        return False

    def choose_level(self, level: int) -> bool:
        """
        选择关卡难度
        """
        log.info("选择关卡")
        # 避免低性能设备加载过慢
        time.sleep(6)  # 等待界面加载

        if cfg.currencywars_rank_difficulty == "current":
            log.info("保持当前职级，不调整关卡难度")
            return True
        elif cfg.currencywars_rank_difficulty == "highest":
            for _ in range(10):
                if auto.click_element("返回最高职级", "text", crop=(1326 / 1920, 947 / 1080, 137 / 1920, 37 / 1080)):
                    time.sleep(2)
                else:
                    break
            return True
        else:
            # pos = auto.find_element("./assets/images/screen/currency_wars/level_down.png", "image", 100000)
            pos = auto.find_element((936 / 1920, 830 / 1080, 45 / 1920, 31 / 1080), "crop")
            for _ in range(100):
                if auto.find_element(f"./assets/images/screen/currency_wars/level_1.png", "image", 0.95, crop=(440.0 / 1920, 892.0 / 1080, 385.0 / 1920, 137.0 / 1080)):
                    log.info(f"已选择敌人难度为1的关卡")
                    return True
                if pos is None:
                    log.error("未找到难度选择按钮，无法选择关卡难度")
                    return False
                auto.click_element_with_pos(pos, cnt=10)
            log.error(f"选择敌人难度为1的关卡失败")
            return False

    def loop(self) -> bool:
        """
        货币战争任务主循环
        """
        self.screenshot = None  # 任务截图
        self.result = None  # 重置结果
        self.peipei_count = 0  # 重置佩佩计数
        self.diamond_count = 0  # 重置财富宝钻计数
        self.current_level = 0  # 重置当前可部署角色等级
        self.current_stage = "0-0"  # 重置当前关卡阶段
        self.need_exit = False  # 是否需要退出
        self._stage_unchanged_count = 0  # 重置阶段未变化计数
        self._unrecognized_stage_count = 0  # 重置未识别阶段计数
        self.need_restart = False  # 是否需要重开
        self.forward_characters = []  # 重置前台角色
        self.backward_characters = []  # 重置后台角色
        self.prepare_characters = []  # 重置备战席角色
        self.reset_tracked_characters()
        self.has_aglaea_three_star = False  # 重置阿格莱雅三星标志
        self.allow_buy_experience = False  # 重置允许购买经验标志
        self.allow_equip_weapons = False  # 重置允许装备武器标志
        self.propeller_count = 0  # 重置螺旋桨数量
        self.shoe_count = 0  # 重置鞋子数量
        self.has_seele = False  # 重置希儿拥有标志
        self.has_seele_three_star = False  # 重置希儿三星标志
        self.seele_firestorm_count = 0  # 重置火力风暴潮数量
        self.seele_chainsaw_count = 0  # 重置高周波电锯数量
        self.allow_seele_equip_weapons = False  # 重置希儿策略装备武器标志
        self.seele_missing_basic_equips = set()  # 重置缺失初级装备
        self.update_backward()

        start_time = time.monotonic()
        last_activity = start_time
        self._last_battle_keepalive = start_time
        timeout = 60 * 120  # 120分钟超时
        while True:
            # 检查超时
            if time.monotonic() - start_time > timeout:
                self.abort_run("货币战争主循环超时（120分钟），停止任务")

            if cfg.cloud_game_enable:
                self.check_connection()
            active = False
            try:
                active = self.check_main_screen() is True
            except Exception as e:
                log.error(f"货币战争主循环出现异常：{e}，尝试退出")

                # 保存调试截图
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                os.makedirs("./debug", exist_ok=True)
                debug_path = f"./debug/currency_wars_exception_{timestamp}.png"
                auto.take_screenshot()
                auto.screenshot.save(debug_path)
                log.error(f"已保存调试截图至 {debug_path}，请将该截图和日志发送给开发者以便排查问题")

                self.give_up_and_settle()

            for check in (self.check_investment_environment, self.check_auto_battle,
                          self.check_click_continue, self.check_supply_phase):
                active = (check() is True) or active
            if self.check_return_home():
                end_time = time.monotonic()
                elapsed_time = end_time - start_time
                minutes = int(elapsed_time // 60)
                seconds = int(elapsed_time % 60)
                log.info(f"本次货币战争用时：{minutes} 分钟 {seconds} 秒")
                return self.result if self.result is not None else False

            now = time.monotonic()
            # 自动战斗可以长时间没有点击；可识别的战斗画面不属于未知界面。
            if active:
                last_activity = now
                self._last_battle_keepalive = now
            elif self.check_battle_keepalive():
                last_activity = now
            elif now - last_activity >= 300:
                self.abort_run("货币战争连续5分钟未识别到可处理界面或战斗画面，停止任务")
            time.sleep(4)

    def check_battle_keepalive(self) -> bool:
        """仅在新截图确认战斗时，向云游戏发送低频、无按键的鼠标移动。"""
        if not auto.find_element("./assets/images/forgottenhall/pause.png", "image", 0.9):
            # 货币战争的暂停图标与忘却之庭模板不同，使用实际战斗状态文字兜底。
            if not auto.find_element(("敌方行动中", "我方行动中"), "text", crop=(0.85, 0.89, 0.15, 0.08)):
                return False
            # 文字识别使用了局部截图，恢复完整截图用于下面的归一化坐标换算。
            if not auto.take_screenshot():
                return False
        if not cfg.cloud_game_enable:
            return True

        now = time.monotonic()
        if self._last_battle_keepalive is None:
            self._last_battle_keepalive = now
        if now - self._last_battle_keepalive < 60:
            return True

        # 使用战斗检测的完整截图换算坐标，两个不同的点保证产生实际移动。
        # 只发 mouseMoved；不点击、不拖动、不按键，不切换自动战斗/倍速/暂停。
        # 在主循环内同步执行，避免后台线程与商店、部署、结算操作争用输入。
        for crop in ((0.49, 0.05, 0.002, 0.002), (0.50, 0.05, 0.002, 0.002)):
            position = auto.find_element(crop, "crop", take_screenshot=False, need_ocr=False)
            if position:
                auto.click_element_with_pos(position, action="move")
        self._last_battle_keepalive = now
        log.info("云游戏战斗保活：发送鼠标移动")
        return True

    def abort_run(self, reason: str):
        """保留现场并中止整个任务，不能在未知状态下继续领奖或开局。"""
        log.error(reason)
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            os.makedirs("./debug", exist_ok=True)
            debug_path = f"./debug/currency_wars_stalled_{timestamp}.png"
            auto.take_screenshot()
            auto.screenshot.save(debug_path)
            log.error(f"已保存现场截图至 {debug_path}")
        except Exception as exc:
            log.warning(f"保存货币战争现场截图失败：{exc}")
        raise RuntimeError(reason)

    def check_connection(self):
        if auto.find_element("加载失败", "text", include=True):
            if auto.find_element("立即切换", "text", take_screenshot=False, need_ocr=False):
                self.abort_run("云游戏加载失败，请检查兼容模式后重试货币战争")
        # 使用新截图；必须同时识别断线标题和退出按钮，避免匹配背景文字。
        if auto.find_element("连接中断", "text", include=True):
            if auto.find_element("退出游戏", "text", take_screenshot=False, need_ocr=False):
                self.abort_run("云游戏连接中断，请重新连接游戏后重试货币战争")

    def check_main_screen(self):
        """
        检查并处理主界面逻辑
        """
        exit_crop = (3.0 / 1920, 37.0 / 1080, 104.0 / 1920, 57.0 / 1080)
        if auto.find_element("./assets/images/screen/currency_wars/exit.png", "image", 0.9, crop=exit_crop):
            start_time = time.monotonic()
            while time.monotonic() - start_time < (4 if cfg.currencywars_strategy == "default" else 7):
                self.click_origin()
                time.sleep(0.1)
            if auto.find_element("./assets/images/screen/currency_wars/exit.png", "image", 0.9, crop=exit_crop):
                if auto.click_element('收起', 'text', None, 1, crop=(1593.0 / 1920, 959.0 / 1080, 60.0 / 1920, 43.0 / 1080)):
                    time.sleep(2)
                _t0 = _tl = time.monotonic()
                _marks = []

                def _mark(label):
                    nonlocal _tl
                    now = time.monotonic()
                    _marks.append(f"{label}{now - _tl:.0f}")
                    _tl = now

                self.check_festival_star_popup()
                self.identify_current_stage()
                if self.need_exit:
                    self.give_up_and_settle()
                    self.need_exit = False
                    return
                if self.collect_reward() is False:
                    # 已识别备战界面；留待下一轮重试，不能直接购买或出战。
                    return True
                _mark("领奖")
                self.check_box()
                self.check_character_status()
                _mark("识别")
                log.info(f"当前关卡阶段: {self.current_stage}")
                if cfg.currencywars_strategy == "aglaea":
                    if self.current_stage == "1-1":
                        self.mark_tracked_characters(self.prepare_characters, "准备席")

                        self.sell_characters_aglaea_strategy()
                        self.buy_aglaea(only_aglaea=not self.has_tracked_character("阿格莱雅"))
                        if not self.has_tracked_character("阿格莱雅"):
                            log.info("未能获得阿格莱雅，尝试重开")
                            self.give_up_and_settle()
                            self.need_exit = False
                            self.need_restart = True
                            return

                    else:
                        self.sell_characters_aglaea_strategy()
                        if (not self.has_aglaea_three_star
                                or not self.has_tracked_character("风堇")
                                or not self.has_tracked_character("缇宝")
                                or not self.has_tracked_character("藿藿")):
                            self.buy_aglaea()
                        else:
                            self.buy_aglaea2()

                elif cfg.currencywars_strategy == "seele":
                    self.mark_tracked_characters(self.prepare_characters, "准备席")
                    self.mark_tracked_characters(self.forward_characters, "前台")
                    self.mark_tracked_characters(self.backward_characters, "后台")
                    # 购买前出售不需要的角色
                    if self.has_seele:
                        self.sell_characters_seele_strategy()
                    # 记录购买前是否已拥有希儿
                    had_seele = self.has_seele
                    # 购买希儿和策略角色
                    self.buy_seele()
                    # 如果是第一次获得希儿，额外出售一次不需要的角色
                    if not had_seele and self.has_seele:
                        log.info("首次获得希儿，立即出售多余角色")
                        self.sell_characters_seele_strategy()

                elif cfg.currencywars_strategy == "default":
                    # 默认策略：商店里出现已拥有的角色就买，凑三合一升星
                    # 买主攻羁绊角色和重复卡合并在一次开店里（原来每回合开关商店两次）
                    self.buy_bond_targets()

                self.buy_trailblaze_shop()
                _mark("商店")

                skip_buy_experience = False
                if cfg.currencywars_strategy == "aglaea" and not self.allow_buy_experience:
                    skip_buy_experience = True
                if not skip_buy_experience:
                    self.buy_experience()
                _mark("经验")
                self.deploy_and_optimize()
                _mark("部署")

                if cfg.currencywars_strategy == "aglaea":
                    self.equip_aglaea()
                elif cfg.currencywars_strategy == "seele":
                    self.equip_seele()

                # if cfg.currencywars_strategy == "aglaea" and self.current_stage.startswith("1-9") and (self.shoe_count < 2):
                #     log.info("当前策略为阿格莱雅，未达到指定装备条件，尝试重开")
                #     self.give_up_and_settle()
                #     self.need_exit = False
                #     return

                self.sell_characters()
                self.trim_bench()
                _mark("出售")
                skip_equip_weapons = False
                if cfg.currencywars_strategy == "aglaea" and not self.allow_equip_weapons:
                    skip_equip_weapons = True
                elif cfg.currencywars_strategy == "seele" and not self.allow_seele_equip_weapons:
                    skip_equip_weapons = True
                if not skip_equip_weapons:
                    self.equip_weapons()
                _mark("装备")
                self.check_festival_star_popup()
                self.choose_protagonist()
                self.reselect_protagonist_guide()
                self.reselect_aura_for_5cost()
                self.fix_bench_full()
                self.fix_over_limit()
                if self.handle_tea_break():
                    return True
                _mark("光环等")
                if cfg.currencywars_strategy == "default":
                    self._last_bond_list = self.read_bond_list()
                    if self._last_bond_list:
                        tg = self.bond_targets()
                        log.info("羁绊列表：" + " ".join(f"{b}{n}" + (f"/{tg[b]}" if b in tg else "") for b, n in self._last_bond_list.items())
                                 + ("（没对上：" + " ".join(getattr(self, "_bond_list_unknown", [])) + "）" if getattr(self, "_bond_list_unknown", None) else ""))
                        self._bond_list_unknown = []
                        # 和按角色库推算的上场羁绊对一下，对不上就把上场名单打出来（找跟踪错位的原因）
                        fielded = [c.name for c in self.forward_characters + self.backward_characters if c.name]
                        est = {}
                        for n in set(fielded):
                            for b in self.char_db().get(n, {}).get("bonds", []):
                                est[b] = est.get(b, 0) + 1
                        diff = [f"{b} 推算{est.get(b, 0)}/实际{n}" for b, n in self._last_bond_list.items() if est.get(b, 0) != n]
                        if diff:
                            log.warning("羁绊对不上：" + "、".join(diff) + "；上场：" + " ".join(fielded) + "；备战席：" + " ".join(c.name for c in self.prepare_characters if c.name))
                            if any(est.get(b, 0) > n for b, n in self._last_bond_list.items()):
                                # 实际比推算少 = 记录的上场名单和游戏里不一致（拖动没成功/被游戏挪了位置），下回合整场重新识别
                                # （实际比推算多是正常的：共舞、从师、星徽都会额外加羁绊）
                                log.warning("上场名单可能错位，下回合重新识别前后台")
                                self.forward_characters = []
                                self.backward_characters = []
                log.info(f"准备阶段用时 {time.monotonic() - _t0:.0f}s（{' '.join(_marks)}），出战")
                auto.click_element(('出战', '跳过'), 'text', None, 10, crop=(1740 / 1920, 709 / 1080, 177 / 1920, 80 / 1080), include=True)
                time.sleep(1)
                self.fix_bench_full()  # 提示是点出战后才冒出来的
                time.sleep(1)
                if auto.click_element("本局不再提示", "text", crop=(905 / 1920, 571 / 1080, 171 / 1920, 50 / 1080)):
                    time.sleep(1)
                auto.click_element("./assets/images/zh_CN/base/confirm.png", "image", 0.9)
                return True

    def sell_characters_aglaea_strategy(self):
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        char_list = {"阿格莱雅", "风堇", "缇宝", "藿藿", "星期日", "符玄", "银狼", "花火", "昔涟", "瓦尔特"}
        if remembrance_trailblazer_name:
            char_list.add(remembrance_trailblazer_name)
        for idx, char in enumerate(self.prepare_characters):
            if char.name and char.name not in char_list:
                log.info(f"准备席角色 {char.name} 不符合条件，尝试出售")
                if self._sell_single_character(idx):
                    # 出售成功，清空该位置的角色信息
                    self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)

    def sell_characters_seele_strategy(self):
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        char_list = {"希儿", "瓦尔特", "刻律德菈", "符玄", "花火", "佩拉", "风堇", "缇宝"}
        if remembrance_trailblazer_name:
            char_list.add(remembrance_trailblazer_name)
        # 出售前台不符合条件的角色
        for idx, char in enumerate(self.forward_characters):
            if char.name and char.name not in char_list:
                log.info(f"前台角色 {char.name} 不符合条件，尝试出售")
                if self._sell_single_character(idx, self.forward_pos):
                    self.forward_characters[idx] = CurrencyWarsCharacter(None, None)
        # 出售后台不符合条件的角色
        for idx, char in enumerate(self.backward_characters):
            if char.name and char.name not in char_list:
                log.info(f"后台角色 {char.name} 不符合条件，尝试出售")
                if self._sell_single_character(idx, self.backward_pos):
                    self.backward_characters[idx] = CurrencyWarsCharacter(None, None)
        # 出售备战席不符合条件的角色，以及重复的策略内角色（希儿除外）
        deployed_names = set()
        for char in self.forward_characters:
            if char.name:
                deployed_names.add(char.name)
        for char in self.backward_characters:
            if char.name:
                deployed_names.add(char.name)
        for idx, char in enumerate(self.prepare_characters):
            if not char.name:
                continue
            if char.name not in char_list:
                log.info(f"准备席角色 {char.name} 不符合条件，尝试出售")
                if self._sell_single_character(idx):
                    self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)
            elif char.name != "希儿" and char.name in deployed_names:
                log.info(f"准备席角色 {char.name} 已在前/后台，出售重复")
                if self._sell_single_character(idx):
                    self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)

    def buy_aglaea(self, only_aglaea=False):
        shop_button_crop = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)
        aglaea3_img = "./assets/images/share/aglaea/aglaea3.png"
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        # 打开商店
        log.info("打开商店购买角色")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        money = self.check_money()
        buy_anything = False
        shop_scan_dirty = True

        def refresh_shop_scan_if_needed():
            nonlocal shop_scan_dirty
            if shop_scan_dirty:
                self._get_ocr_texts_in_crop(shop_crop)
                shop_scan_dirty = False

        while True:
            refresh_shop_scan_if_needed()

            if not self.has_aglaea_three_star and auto.find_element(aglaea3_img, "image", 0.9, take_screenshot=False, need_ocr=False):
                log.info("检测到阿格莱雅三星")
                self.has_aglaea_three_star = True
            elif not self.has_aglaea_three_star and self.can_afford_character(money, "阿格莱雅") and self.try_buy_character("阿格莱雅", wait_time=5):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "阿格莱雅")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("风堇") and self.can_afford_character(money, "风堇") and self.try_buy_character("风堇"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "风堇")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("缇宝") and self.can_afford_character(money, "缇宝") and self.try_buy_character("缇宝"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "缇宝")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("藿藿") and self.can_afford_character(money, "藿藿") and self.try_buy_character("藿藿"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "藿藿")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("星期日") and self.can_afford_character(money, "星期日") and self.try_buy_character("星期日"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "星期日")
                shop_scan_dirty = True
            elif (remembrance_trailblazer_name and not only_aglaea and not self.has_tracked_character(remembrance_trailblazer_name)
                  and self.can_afford_character(money, remembrance_trailblazer_name)
                  and self.try_buy_character(remembrance_trailblazer_name)):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, remembrance_trailblazer_name)
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("符玄") and self.can_afford_character(money, "符玄") and self.try_buy_character("符玄"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "符玄")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("银狼") and self.can_afford_character(money, "银狼") and self.try_buy_character("银狼"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "银狼")
                shop_scan_dirty = True
            elif not only_aglaea and not self.has_tracked_character("花火") and self.can_afford_character(money, "花火") and self.try_buy_character("花火"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "花火")
                shop_scan_dirty = True
            elif (self.has_aglaea_three_star and self.has_tracked_character("风堇")
                  and self.has_tracked_character("缇宝") and self.has_tracked_character("藿藿")):
                self.allow_buy_experience = True
                break
            elif (not self.has_aglaea_three_star and money >= 3) or (not only_aglaea and money >= 4):
                auto.press_key('d')
                log.info("刷新角色列表")
                time.sleep(1)
                money -= 2
                shop_scan_dirty = True
            else:
                break
        # 关闭商店
        log.info("关闭商店")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        if buy_anything:
            self.check_character_status()
        if self.allow_buy_experience:
            self.buy_aglaea2()

    def buy_aglaea2(self):
        shop_button_crop = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        # 打开商店
        log.info("打开商店购买角色")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        money = self.check_money()
        buy_anything = False
        shop_scan_dirty = True

        def refresh_shop_scan_if_needed():
            nonlocal shop_scan_dirty
            if shop_scan_dirty:
                self._get_ocr_texts_in_crop(shop_crop)
                shop_scan_dirty = False

        while True:
            refresh_shop_scan_if_needed()

            if not self.has_tracked_character("瓦尔特") and self.can_afford_character(money, "瓦尔特") and self.try_buy_character("瓦尔特"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "瓦尔特")
                shop_scan_dirty = True
                continue

            if not self.has_tracked_character("昔涟") and self.can_afford_character(money, "昔涟") and self.try_buy_character("昔涟"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "昔涟")
                shop_scan_dirty = True
                continue

            if not self.has_tracked_character("星期日") and self.can_afford_character(money, "星期日") and self.try_buy_character("星期日"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "星期日")
                shop_scan_dirty = True
                continue

            if (remembrance_trailblazer_name and not self.has_tracked_character(remembrance_trailblazer_name)
                    and self.can_afford_character(money, remembrance_trailblazer_name)
                    and self.try_buy_character(remembrance_trailblazer_name)):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, remembrance_trailblazer_name)
                shop_scan_dirty = True
                continue

            if not self.has_tracked_character("符玄") and self.can_afford_character(money, "符玄") and self.try_buy_character("符玄"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "符玄")
                shop_scan_dirty = True
                continue

            if not self.has_tracked_character("银狼") and self.can_afford_character(money, "银狼") and self.try_buy_character("银狼"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "银狼")
                shop_scan_dirty = True
                continue

            if not self.has_tracked_character("花火") and self.can_afford_character(money, "花火") and self.try_buy_character("花火"):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "花火")
                shop_scan_dirty = True
                continue

            # if money >= 2 and auto.click_element("风堇", "text", crop=shop_crop):
            #     log.info("尝试购买风堇")
            #     buy_anything = True
            #     self.has_hyacine = True
            #     time.sleep(1)
            #     money -= 2

            # if money >= 2 and auto.click_element("缇宝", "text", crop=shop_crop):
            #     log.info("尝试购买缇宝")
            #     buy_anything = True
            #     self.has_tribbie = True
            #     time.sleep(1)
            #     money -= 2

            # if money >= 1 and auto.click_element("藿藿", "text", crop=shop_crop):
            #     log.info("尝试购买藿藿")
            #     buy_anything = True
            #     self.has_huohuo = True
            #     time.sleep(1)
            #     money -= 1

            if ((cfg.currencywars_type == "normal" and self.current_stage != "3-7") or (cfg.currencywars_type == "overclock" and self.current_stage != "3-5")) and self.current_level < 9:
                break
            else:
                if money >= 7 and (not self.has_tracked_character("瓦尔特") or not self.has_tracked_character("昔涟")):
                    auto.press_key('d')
                    log.info("刷新角色列表")
                    time.sleep(1)
                    money -= 2
                    shop_scan_dirty = True
                else:
                    break

        # 关闭商店
        log.info("关闭商店")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        if buy_anything:
            self.check_character_status()

    def buy_seele(self):
        shop_button_crop = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)
        seele3_img = "./assets/images/share/aglaea/seele3.png"
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()

        # 确定当前阶段
        if not self.has_seele:
            phase = 1 if self.current_level < 6 else 2
        elif not self.has_seele_three_star:
            phase = 3 if self.current_level < 7 else 4
        else:
            phase = 5 if self.current_level < 9 else 6
        log.info(f"希儿策略购买阶段: {phase}")

        # 打开商店
        log.info("打开商店购买角色")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        money = self.check_money()
        buy_anything = False
        shop_scan_dirty = True

        def refresh_shop_scan_if_needed():
            nonlocal shop_scan_dirty
            if shop_scan_dirty:
                self._get_ocr_texts_in_crop(shop_crop)
                shop_scan_dirty = False

        # 策略内需要的角色（按购买费用从高到低排列）
        strategy_characters = ["瓦尔特", "刻律德菈", "符玄", "花火", "佩拉", "风堇", "缇宝"]
        if remembrance_trailblazer_name:
            strategy_characters.insert(2, remembrance_trailblazer_name)  # 费用4，排在刻律德菈之前

        def try_buy_strategy_characters():
            """尝试购买策略内需要的角色，返回是否购买了任何角色"""
            nonlocal money, buy_anything, shop_scan_dirty
            bought = False
            for name in strategy_characters:
                if not self.has_tracked_character(name) and self.can_afford_character(money, name) and self.try_buy_character(name):
                    buy_anything = True
                    money = self.spend_character_purchase_cost(money, name)
                    shop_scan_dirty = True
                    bought = True
            return bought

        while True:
            refresh_shop_scan_if_needed()

            # 检测希儿三星
            if not self.has_seele_three_star and auto.find_element(seele3_img, "image", 0.9, take_screenshot=False, need_ocr=False):
                log.info("检测到希儿三星")
                self.has_seele_three_star = True
                # 三星后更新阶段
                if self.current_level < 9:
                    phase = 5
                else:
                    phase = 6
                continue

            # 尝试购买希儿
            if not self.has_seele_three_star and self.can_afford_character(money, "希儿") and self.try_buy_character("希儿", wait_time=5):
                buy_anything = True
                money = self.spend_character_purchase_cost(money, "希儿")
                shop_scan_dirty = True
                if not self.has_seele:
                    self.has_seele = True
                    log.info("成功获得希儿")
                continue

            # 阶段1: 无希儿 + 等级<6 — 买希儿+策略角色，不刷新
            if phase == 1:
                try_buy_strategy_characters()
                break

            # 阶段2: 无希儿 + 等级>=6 — 买希儿+策略角色，刷新直到钱<5
            elif phase == 2:
                try_buy_strategy_characters()
                if money >= 5:
                    auto.press_key('d')
                    log.info("刷新角色列表")
                    time.sleep(1)
                    money -= 2
                    shop_scan_dirty = True
                else:
                    break

            # 阶段3: 有希儿+未三星+等级<7 — 买希儿+策略角色，不刷新
            elif phase == 3:
                try_buy_strategy_characters()
                break

            # 阶段4: 有希儿+未三星+等级>=7 — 买希儿+策略角色，允许刷新
            elif phase == 4:
                try_buy_strategy_characters()
                if money >= 3:
                    auto.press_key('d')
                    log.info("刷新角色列表")
                    time.sleep(1)
                    money -= 2
                    shop_scan_dirty = True
                else:
                    break

            # 阶段5: 三星+等级<9 — 只买策略角色，不刷新
            elif phase == 5:
                try_buy_strategy_characters()
                break

            # 阶段6: 三星+等级>=9 — 买策略角色，缺角色且钱>=7时刷新
            elif phase == 6:
                try_buy_strategy_characters()
                # 检查是否还缺策略角色
                missing = False
                for name in strategy_characters:
                    if not self.has_tracked_character(name):
                        missing = True
                        break
                if missing and money >= 7:
                    auto.press_key('d')
                    log.info("刷新角色列表")
                    time.sleep(1)
                    money -= 2
                    shop_scan_dirty = True
                else:
                    break

        # 关闭商店
        log.info("关闭商店")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        if buy_anything:
            self.check_character_status()

    def buy_owned_duplicates(self, keep_money: int = 0):
        """
        默认策略的购买逻辑：商店里出现已拥有（前台/后台/备战席）的角色就买，凑三合一升星。
        费用取自角色信息缓存；钱不够就跳过，不刷新商店。
        """
        owned = {}
        for char in self.forward_characters + self.backward_characters + self.prepare_characters:
            if char.name and char.name in self.character_info_cache:
                owned[char.name] = self.character_info_cache[char.name].money
        if not owned:
            return
        money = self.check_money()
        if money - keep_money < min(owned.values()):
            return
        shop_button_crop = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)
        log.info("打开商店购买已拥有的角色")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        bought = False
        for _ in range(5):
            texts = self._get_ocr_texts_in_crop(shop_crop)
            target = None
            # 先买贵的（一般更强）
            for name, cost in sorted(owned.items(), key=lambda x: -x[1]):
                if money - cost >= keep_money and name in texts:
                    target = name
                    break
            if not target or not self.try_buy_character(target):
                break
            money -= owned[target]
            bought = True
        log.info("关闭商店")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        if bought:
            self.check_character_status()

    def buy_trailblaze_shop(self):
        """
        入画奇航：开拓银轨羁绊的「开拓商店」用羁绊自己的货币，不花金币。
        前台左侧出现「有商品可购买」时打开商店，把能买的都买掉（买得越多越快解锁帕姆王），再收起面板。
        """
        if not auto.find_element("有商品可购买", "text", None, 1, include=True, crop=(300 / 1920, 230 / 1080, 360 / 1920, 160 / 1080)):
            return
        log.info("开拓商店有商品可购买，打开商店")
        title_crop = (900 / 1920, 50 / 1080, 300 / 1920, 60 / 1080)
        opened = auto.click_element("打开商店", "text", None, 1, include=True, crop=(300 / 1920, 230 / 1080, 360 / 1920, 160 / 1080))
        if not opened or not auto.find_element("开拓商店", "text", None, 3, include=True, crop=title_crop):
            # 也可以点开拓银轨羁绊名右边的小开关
            bond = auto.find_element("开拓银轨", "text", None, 1, include=True, crop=(0, 200 / 1080, 260 / 1920, 600 / 1080))
            if bond:
                auto.click_element_with_pos(bond, offset=(int(108 / 1920 * auto.screenshot.width / getattr(auto, "screenshot_scale_factor", 1)), 0))
                time.sleep(1.5)
        if not auto.find_element("开拓商店", "text", None, 3, include=True, crop=title_crop):
            log.warning("开拓商店没有打开")
            return
        # 首次是卡背，先翻开
        if auto.click_element("跃迁", "text", None, 1, include=True, crop=(900 / 1920, 340 / 1080, 360 / 1920, 80 / 1080)):
            time.sleep(1.5)
        currency_crop = (520 / 1920, 92 / 1080, 140 / 1920, 40 / 1080)
        card_crops = [(450 / 1920, 170 / 1080, 200 / 1920, 200 / 1080), (700 / 1920, 170 / 1080, 200 / 1920, 200 / 1080),
                      (950 / 1920, 170 / 1080, 200 / 1920, 200 / 1080), (1200 / 1920, 170 / 1080, 200 / 1920, 200 / 1080)]
        last = None
        for _ in range(8):
            text = auto.get_single_line_text(crop=currency_crop)
            currency = int(text) if text and text.isdigit() else None
            log.info(f"开拓商店货币：{currency}")
            if currency is None or currency < 15 or currency == last:
                break
            last = currency
            for card in card_crops:
                auto.click_element(card, "crop")
                time.sleep(1)
        # 收起面板：点开拓银轨羁绊旁的开关
        bond = auto.find_element("开拓银轨", "text", None, 1, include=True, crop=(0, 200 / 1080, 260 / 1920, 600 / 1080))
        if bond:
            auto.click_element_with_pos(bond, offset=(int(108 / 1920 * auto.screenshot.width / getattr(auto, "screenshot_scale_factor", 1)), 0))
            time.sleep(1.5)
        self.check_character_status()

    def equip_aglaea(self):
        # 轮滑鞋
        shoe_img = "./assets/images/share/aglaea/shoe.png"
        # 反重力皮靴
        boots_img = "./assets/images/share/aglaea/boots.png"
        # 光速螺旋桨
        propeller_img = "./assets/images/share/aglaea/propeller.png"
        # 拆装扳手
        wrench_img = "./assets/images/share/aglaea/wrench.png"
        # 拆装扳手2
        wrench2_img = "./assets/images/share/aglaea/wrench2.png"
        # 冶金炉
        stove_img = "./assets/images/share/aglaea/stove.png"
        # 好运令牌
        token_img = "./assets/images/share/aglaea/token.png"

        # 简易装备列表
        simple_equip_list = [
            "./assets/images/share/aglaea/e1.png",
            "./assets/images/share/aglaea/e2.png",
            "./assets/images/share/aglaea/e3.png",
            "./assets/images/share/aglaea/e4.png",
            "./assets/images/share/aglaea/e5.png",
            "./assets/images/share/aglaea/e6.png",
            "./assets/images/share/aglaea/e7.png",
        ]

        equip_crop = (1386 / 1920, 93 / 1080, 514 / 1920, 610 / 1080)
        aglaea_position = self.find_deployed_character_position("阿格莱雅")
        if not aglaea_position:
            log.warning("未找到阿格莱雅位置，跳过装备阿格莱雅流程")
            return

        def try_equip(from_position, to_position):
            time.sleep(2)
            auto.click_element_with_pos(from_position, action="down")
            time.sleep(1)
            auto.click_element_with_pos(to_position, action="move")
            time.sleep(1)
            auto.mouse_up()
            time.sleep(2)

        # 员工投影仪和完美投影仪：阿格莱雅未三星时使用
        if not self.has_aglaea_three_star:
            projector_img = "./assets/images/share/aglaea/projector.png"
            projector2_img = "./assets/images/share/aglaea/projector2.png"
            for _ in range(5):
                if result := auto.find_element(projector2_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到完美投影仪，尝试使用")
                    try_equip(result, aglaea_position)
                elif result := auto.find_element(projector_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到员工投影仪，尝试使用")
                    try_equip(result, aglaea_position)
                else:
                    break

        if self.shoe_count < 4:
            cnt = 0
            for _ in range(10):
                if result := auto.find_element(token_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到好运令牌，尝试使用好运令牌")
                    try_equip(result, aglaea_position)
                    if auto.click_element("反重力皮靴", "text", crop=(534 / 1920, 247 / 1080, 1129 / 1920, 45 / 1080)):
                        time.sleep(2)
                        log.info("成功使用好运令牌，获得了反重力皮靴")
                        cnt += 1
                        # 只需要一双皮靴
                        if self.shoe_count >= 2:
                            break
                        # 获得两双皮靴
                        elif cnt >= 2:
                            break
                    else:
                        auto.click_element((534 / 1920, 247 / 1080, 153 / 1920, 45 / 1080), "crop")
                    time.sleep(2)
                else:
                    break

            for _ in range(3):
                if self.shoe_count < 4:
                    if result := auto.find_element(boots_img, "image", 0.9, crop=equip_crop):
                        if self.shoe_count in (0, 1, 2):
                            log.info("检测到反重力皮靴，尝试装备反重力皮靴")
                            try_equip(result, aglaea_position)
                            self.shoe_count += 2
                        elif self.shoe_count == 3:
                            # 判断是否存在拆装扳手，移动到角色身上拆掉已有装备
                            if result_wrench2 := auto.find_element(wrench2_img, "image", 0.9, crop=equip_crop):
                                log.info("检测到拆装扳手2，尝试使用拆装扳手2")
                                try_equip(result_wrench2, aglaea_position)
                                self.shoe_count = 0
                                result = auto.find_element(boots_img, "image", 0.9, crop=equip_crop)
                                if result:
                                    log.info("尝试装备反重力皮靴")
                                    try_equip(result, aglaea_position)
                                    self.shoe_count += 2
                            elif result_wrench := auto.find_element(wrench_img, "image", 0.9, crop=equip_crop):
                                log.info("检测到拆装扳手，尝试使用拆装扳手")
                                try_equip(result_wrench, aglaea_position)
                                self.shoe_count = 0
                                result = auto.find_element(boots_img, "image", 0.9, crop=equip_crop)
                                if result:
                                    log.info("尝试装备反重力皮靴")
                                    try_equip(result, aglaea_position)
                                    self.shoe_count += 2
                            else:
                                log.info("未检测到拆装扳手，无法拆除已有装备，跳过装备反重力皮靴")
                                break

        if self.shoe_count < 4:
            for _ in range(10):
                if result := auto.find_element(stove_img, "image", 0.9, crop=equip_crop):
                    for simple_equip in simple_equip_list:
                        if result_simple := auto.find_element(simple_equip, "image", 0.9, crop=equip_crop):
                            log.info("检测到冶金炉，尝试使用冶金炉")
                            try_equip(result, result_simple)
                            time.sleep(2)
                            break
                else:
                    break

        for _ in range(4):
            if self.shoe_count < 4:
                if result := auto.find_element(shoe_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到轮滑鞋，尝试装备轮滑鞋")
                    try_equip(result, aglaea_position)
                    self.shoe_count += 1
                else:
                    break

        if result := auto.find_element(propeller_img, "image", 0.9, crop=equip_crop):
            log.info("检测到螺旋桨，尝试装备螺旋桨")
            try_equip(result, aglaea_position)
            self.propeller_count = 1

        if self.shoe_count >= 4 and self.propeller_count >= 1:
            self.allow_equip_weapons = True

    def equip_seele(self):
        # 轮滑鞋
        shoe_img = "./assets/images/share/aglaea/shoe.png"
        # 折叠小刀
        knife_img = "./assets/images/share/aglaea/e7.png"
        # 幸运星
        star_img = "./assets/images/share/aglaea/e1.png"
        # 火力风暴潮 = 轮滑鞋 + 折叠小刀
        firestorm_img = "./assets/images/share/aglaea/firestorm.png"
        # 高周波电锯 = 幸运星 + 折叠小刀
        chainsaw_img = "./assets/images/share/aglaea/chainsaw.png"
        # 好运令牌
        token_img = "./assets/images/share/aglaea/token.png"
        # 冶金炉
        stove_img = "./assets/images/share/aglaea/stove.png"
        # 简易装备列表
        simple_equip_list = [
            "./assets/images/share/aglaea/e2.png",
            "./assets/images/share/aglaea/e3.png",
            "./assets/images/share/aglaea/e4.png",
            "./assets/images/share/aglaea/e5.png",
            "./assets/images/share/aglaea/e6.png",
        ]

        equip_crop = (1386 / 1920, 93 / 1080, 514 / 1920, 610 / 1080)
        seele_position = self.find_deployed_character_position("希儿")
        if not seele_position:
            log.warning("未找到希儿位置，跳过装备希儿流程")
            # 即使希儿未部署，也扫描装备栏判断缺少哪个初级装备
            if self.seele_firestorm_count < 2:
                has_shoe = bool(auto.find_element(shoe_img, "image", 0.9, crop=equip_crop))
                has_knife_for_firestorm = bool(auto.find_element(knife_img, "image", 0.9, crop=equip_crop))
                if has_shoe and not has_knife_for_firestorm:
                    self.seele_missing_basic_equips.add("折叠小刀")
                    self.seele_missing_basic_equips.discard("轮滑鞋")
                elif not has_shoe and has_knife_for_firestorm:
                    self.seele_missing_basic_equips.add("轮滑鞋")
                    self.seele_missing_basic_equips.discard("折叠小刀")
            if self.seele_chainsaw_count < 1:
                has_star = bool(auto.find_element(star_img, "image", 0.9, crop=equip_crop))
                has_knife_for_chainsaw = bool(auto.find_element(knife_img, "image", 0.9, crop=equip_crop))
                if has_star and not has_knife_for_chainsaw:
                    self.seele_missing_basic_equips.add("折叠小刀")
                    self.seele_missing_basic_equips.discard("幸运星")
                elif not has_star and has_knife_for_chainsaw:
                    self.seele_missing_basic_equips.add("幸运星")
                    self.seele_missing_basic_equips.discard("折叠小刀")
            return

        def try_equip(from_position, to_position):
            time.sleep(2)
            auto.click_element_with_pos(from_position, action="down")
            time.sleep(1)
            auto.click_element_with_pos(to_position, action="move")
            time.sleep(1)
            auto.mouse_up()
            time.sleep(2)

        def try_synthesize(comp1_img, comp2_img, comp1_name, comp2_name):
            """尝试合成装备：将两个初级装备都拖到希儿身上，自动合成"""
            result1 = auto.find_element(comp1_img, "image", 0.9, crop=equip_crop)
            result2 = auto.find_element(comp2_img, "image", 0.9, crop=equip_crop)
            if result1 and result2:
                # 将第一个装备拖到希儿身上
                try_equip(result1, seele_position)
                # 将第二个装备拖到希儿身上，自动触发合成
                result2 = auto.find_element(comp2_img, "image", 0.9, crop=equip_crop)
                try_equip(result2, seele_position)
                self.seele_missing_basic_equips.discard(comp1_name)
                self.seele_missing_basic_equips.discard(comp2_name)
                return True
            elif result1 and not result2:
                self.seele_missing_basic_equips.discard(comp1_name)
                self.seele_missing_basic_equips.add(comp2_name)
            elif not result1 and result2:
                self.seele_missing_basic_equips.add(comp1_name)
                self.seele_missing_basic_equips.discard(comp2_name)
            return False

        # 员工投影仪和完美投影仪：希儿未三星时使用
        if not self.has_seele_three_star:
            projector_img = "./assets/images/share/aglaea/projector.png"
            projector2_img = "./assets/images/share/aglaea/projector2.png"
            for _ in range(5):
                if result := auto.find_element(projector2_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到完美投影仪，尝试使用")
                    try_equip(result, seele_position)
                elif result := auto.find_element(projector_img, "image", 0.9, crop=equip_crop):
                    log.info("检测到员工投影仪，尝试使用")
                    try_equip(result, seele_position)
                else:
                    break

        # 希儿需要2个火力风暴潮和1个高周波电锯
        # 火力风暴潮 = 轮滑鞋 + 折叠小刀
        # 高周波电锯 = 幸运星 + 折叠小刀

        # 优先使用好运令牌选择缺失的高级装备
        token_firestorm_selected = 0
        token_chainsaw_selected = 0
        for _ in range(5):
            # 用临时计数+已装备数判断是否还需要
            need_firestorm = self.seele_firestorm_count + token_firestorm_selected < 2
            need_chainsaw = self.seele_chainsaw_count + token_chainsaw_selected < 1
            if not need_firestorm and not need_chainsaw:
                break
            if result := auto.find_element(token_img, "image", 0.9, crop=equip_crop):
                log.info("检测到好运令牌，尝试使用好运令牌")
                try_equip(result, seele_position)
                # 按缺失优先级选择装备（选择后会出现在装备栏，后续由合成/装备循环处理）
                if need_firestorm and auto.click_element("火力风暴潮", "text", crop=(534 / 1920, 247 / 1080, 1129 / 1920, 45 / 1080)):
                    log.info("使用好运令牌选择了火力风暴潮")
                    token_firestorm_selected += 1
                elif need_chainsaw and auto.click_element("高周波电锯", "text", crop=(534 / 1920, 247 / 1080, 1129 / 1920, 45 / 1080)):
                    log.info("使用好运令牌选择了高周波电锯")
                    token_chainsaw_selected += 1
                else:
                    # 没找到目标装备，随便点一个关闭菜单
                    auto.click_element((534 / 1920, 247 / 1080, 153 / 1920, 45 / 1080), "crop")
                time.sleep(2)
            else:
                break

        if self.seele_firestorm_count < 2 or self.seele_chainsaw_count < 1:
            for _ in range(10):
                if result := auto.find_element(stove_img, "image", 0.9, crop=equip_crop):
                    for simple_equip in simple_equip_list:
                        if result_simple := auto.find_element(simple_equip, "image", 0.9, crop=equip_crop):
                            log.info("检测到冶金炉，尝试使用冶金炉")
                            try_equip(result, result_simple)
                            time.sleep(2)
                            break
                else:
                    break

        # 尝试合成并装备火力风暴潮
        while self.seele_firestorm_count < 2:
            # 先检查是否已有合成好的火力风暴潮
            if result := auto.find_element(firestorm_img, "image", 0.9, crop=equip_crop):
                log.info("检测到火力风暴潮，尝试装备给希儿")
                try_equip(result, seele_position)
                self.seele_firestorm_count += 1
                continue
            # 尝试合成
            if try_synthesize(shoe_img, knife_img, "轮滑鞋", "折叠小刀"):
                log.info("尝试合成火力风暴潮（轮滑鞋+折叠小刀）")
                self.seele_firestorm_count += 1
                continue
            # 没有材料了
            log.info("缺少合成火力风暴潮的材料")
            break

        # 尝试合成并装备高周波电锯
        while self.seele_chainsaw_count < 1:
            # 先检查是否已有合成好的高周波电锯
            if result := auto.find_element(chainsaw_img, "image", 0.9, crop=equip_crop):
                log.info("检测到高周波电锯，尝试装备给希儿")
                try_equip(result, seele_position)
                self.seele_chainsaw_count += 1
                continue
            # 尝试合成
            if try_synthesize(star_img, knife_img, "幸运星", "折叠小刀"):
                log.info("尝试合成高周波电锯（幸运星+折叠小刀）")
                self.seele_chainsaw_count += 1
                continue
            # 没有材料了
            log.info("缺少合成高周波电锯的材料")
            break

        if self.seele_firestorm_count >= 2 and self.seele_chainsaw_count >= 1:
            self.allow_seele_equip_weapons = True
            log.info("希儿装备齐全，允许为其他角色装备武器")

    def give_up_and_settle(self):
        """
        放弃并结算：按ESC键并点击放弃并结算按钮
        """
        log.info("尝试放弃并结算当前对局")
        auto.press_key('esc')
        time.sleep(2)
        if not auto.click_element('放弃并结算', 'text', include=True):
            log.warning("放弃并结算失败，等待一段时间后重试")
            time.sleep(5)
            auto.press_key('esc')
            if not auto.click_element('放弃并结算', 'text', include=True):
                log.error("放弃并结算失败，请检查游戏状态")
                raise RuntimeError("放弃并结算失败")

    def sell_characters(self):
        """
        出售多余角色
        """
        log.info("出售多余角色")
        any_sold = False
        bond_counts = self.owned_bond_counts()

        def keep(char):
            # 主攻羁绊的角色留着等上场名额，不当散件卖
            return self.bond_bonus(char.name, bond_counts) >= 3

        forward_names = set()
        forward_count = 0
        for char in self.forward_characters:
            if char.name:
                forward_names.add(char.name)
                forward_count += 1

        if forward_count == len(self.forward_pos):
            for idx, char in enumerate(self.prepare_characters):
                if char.name and char.pos == "forward" and char.name not in forward_names and not keep(char):
                    log.info(f"出售角色 {char.name}")
                    if self._sell_single_character(idx):
                        # 出售成功，清空该位置的角色信息
                        self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)
                        any_sold = True

        backward_names = set()
        backward_count = 0
        for char in self.backward_characters:
            if char.name:
                backward_names.add(char.name)
                backward_count += 1
        if backward_count == len(self.backward_pos):
            for idx, char in enumerate(self.prepare_characters):
                if char.name and char.pos == "backward" and char.name not in backward_names and not keep(char):
                    log.info(f"出售角色 {char.name}")
                    if self._sell_single_character(idx):
                        # 出售成功，清空该位置的角色信息
                        self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)
                        any_sold = True

        if forward_count == len(self.forward_pos) and backward_count == len(self.backward_pos):
            for idx, char in enumerate(self.prepare_characters):
                if char.name and char.pos == "all" and char.name not in forward_names and char.name not in backward_names and not keep(char):
                    log.info(f"出售角色 {char.name}")
                    if self._sell_single_character(idx):
                        # 出售成功，清空该位置的角色信息
                        self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)
                        any_sold = True

        # 如果有角色出售成功，再执行一次购买经验和部署优化
        if any_sold:
            log.info("检测到角色出售成功，重新执行购买经验和部署优化")
            self.buy_experience()
            self.deploy_and_optimize()
            if cfg.currencywars_strategy == "aglaea":
                self.equip_aglaea()
            elif cfg.currencywars_strategy == "seele":
                self.equip_seele()

    def _sell_single_character(self, idx: int, pos_list=None) -> bool:
        """
        出售单个角色

        参数：
            idx: 角色在列表中的索引
            pos_list: 位置列表（forward_pos/backward_pos/prepare_pos），默认为 prepare_pos

        返回：
            True 表示出售成功，False 表示出售失败
        """
        if pos_list is None:
            pos_list = self.prepare_pos
        position = pos_list[idx]
        auto.click_element(position, "crop")
        time.sleep(0.5)
        if auto.click_element("出售", "text", crop=(1694.0 / 1920, 896.0 / 1080, 141.0 / 1920, 39.0 / 1080), include=True):
            time.sleep(0.5)
            return True
        self.click_origin()
        return False

    def _gray_crop(self, crop):
        auto.take_screenshot(crop=crop)
        return np.asarray(auto.screenshot.convert("L"), dtype=np.int16)

    def equip_by_drag(self):
        """入画奇航：角色面板只有「推荐」参考，没有一键合成/穿戴（旧逻辑找「装备推荐」永远找不到，等于从不穿装备）。
        把右侧物品栏第一格的装备拖到上场角色身上：同一角色两件简易装备自动合成进阶装备，每人 3 格（6 件散件）。
        优先给主攻阵容里费用高的角色，凑满一个再给下一个（成对给才会合成）。"""
        slot = (1815 / 1920, 222 / 1080, 56 / 1920, 56 / 1080)
        column = (1812 / 1920, 215 / 1080, 62 / 1920, 390 / 1080)
        owned = {c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name}
        given = {n: v for n, v in getattr(self, "_equip_given", {}).items() if n in owned}
        counts = self.owned_bond_counts()
        targets = []
        for chars, positions in ((self.forward_characters, self.forward_pos), (self.backward_characters, self.backward_pos)):
            for idx, c in enumerate(chars):
                if c.name and idx < len(positions):
                    targets.append((-((c.money or 0) + self.bond_bonus(c.name, counts)), len(targets), c.name, positions[idx]))
        targets.sort()
        moved, failed = 0, []
        for _ in range(12):
            tgt = next(((n, pos) for _, _, n, pos in targets if given.get(n, 0) < 6 and n not in failed), None)
            if not tgt:
                break
            name, pos = tgt
            before = self._gray_crop(column)
            src = auto.find_element(slot, "crop")
            dst = auto.find_element(pos, "crop")
            if not src or not dst:
                break
            auto.click_element_with_pos(src, action="down")
            time.sleep(0.4)
            auto.click_element_with_pos(dst, action="move")
            time.sleep(0.4)
            auto.mouse_up()
            time.sleep(0.7)
            after = self._gray_crop(column)
            if before.shape == after.shape and float(np.abs(before - after).mean()) < 2.0:
                # 物品栏没变化：要么空了，要么这个角色装不下。换下一个角色再试一次，还不行就是空了
                failed.append(name)
                if len(failed) >= 2:
                    break
                continue
            if failed:
                given[failed[-1]] = 6  # 上一个确实是装不下
                failed = []
            given[name] = given.get(name, 0) + 1
            moved += 1
            log.info(f"装备：拖给 {name}（第 {given[name]} 件）")
        self._equip_given = given
        self.click_origin()
        return moved

    def read_bond_list(self) -> dict:
        """读备战界面左侧的羁绊列表 {羁绊: 人数}（游戏里的真实计数，含星徽等加成；只读得到当前可见的几行）。
        人数 = 图标左下角的角标数字。已激活的羁绊名字下面是各档门槛（如 2/4/6/9），不是人数；
        未激活的是「人数/下一档」。角标没识别到时单独放大再认一次，还认不到就不采信这一行。"""
        try:
            from module.ocr import ocr as _ocr
            from PIL import Image
            auto.take_screenshot()
            full = auto.screenshot.convert("RGB")
            W, H = full.size
            x0, y0 = 0, int(190 / 1080 * H)
            crop = full.crop((x0, y0, int(290 / 1920 * W), int(790 / 1080 * H)))
            result = _ocr.recognize_multi_lines(np.array(crop)) or []
            items = sorted((box[0][1], box[0][0], text.strip()) for box, (text, _c) in result if text.strip())
        except Exception as e:
            log.debug(f"读取羁绊列表失败: {e}")
            return {}
        known = set(BOND_LINK_TARGETS) | {"千星商会"}
        sy = H / 1080
        res = {}
        import difflib
        unknown = []
        for y, x, t0 in items:
            t = t0
            if t not in known:
                # 名字被认错一两个字时按相似度对回去（第五局幻月游戏有 4 人却没出现在读数里）
                close = difflib.get_close_matches(t, known, 1, 0.6) if 2 <= len(t) <= 6 and not re.search(r"[\d/]", t) else []
                if not close:
                    if 2 <= len(t) <= 6 and not re.search(r"[\d/]", t) and x > 60 * sy:
                        unknown.append(t)
                    continue
                t = close[0]
            badge = [int(t2) for y2, x2, t2 in items if 15 * sy < y2 - y < 60 * sy and x2 < x and t2.isdigit() and len(t2) <= 2]
            count = badge[0] if badge else None
            if count is None:
                try:
                    cx, cy, half = x0 + x - 22 * sy, y0 + y + 46 * sy, 18 * sy
                    b = full.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
                    b = b.resize((b.width * 4, b.height * 4), Image.LANCZOS)
                    r = _ocr.recognize_single_line(np.array(b))
                    if r and str(r[0]).strip().isdigit():
                        count = int(str(r[0]).strip())
                except Exception as e:
                    log.debug(f"读取羁绊角标失败: {e}")
            if count is None:
                for yy, xx, tt in items:
                    if 15 * sy < yy - y < 60 * sy and re.match(r"^\d+/\d+$", tt):
                        count = int(tt.split("/")[0])  # 两个数：按「人数/下一档」读
                        break
            if count is not None and count <= 12:
                res[t] = count
        if unknown:
            log.debug("羁绊列表里没对上的名字：" + " ".join(unknown))
            self._bond_list_unknown = unknown
        return res

    def equip_weapons(self):
        """
        装备武器
        """
        if cfg.currencywars_strategy == "default":
            self.equip_by_drag()
            return
        if cfg.currencywars_fast_mode:
            if ((cfg.currencywars_type == "normal"
                 and self.current_stage not in ("1-9", "2-7", "3-7"))
                    or (cfg.currencywars_type == "overclock"
                        and self.current_stage not in ("1-6", "2-5", "3-5"))):
                log.info("当前为速通模式且非首领节点，跳过装备武器步骤")
                return

        character_groups = [
            (self.forward_characters, self.forward_pos),
            (self.backward_characters, self.backward_pos)
        ]

        for characters, positions in character_groups:
            indexed_chars = [(idx, c) for idx, c in enumerate(characters) if c.name]
            indexed_chars.sort(key=lambda item: (-item[1].money, item[0]))
            for idx, _ in indexed_chars:
                self._equip_single_character(positions[idx])

    def _equip_single_character(self, position):
        """
        为单个角色装备武器
        """
        auto.click_element(position, "crop")
        time.sleep(0.5)
        if auto.click_element("装备推荐", "text", crop=(1403.0 / 1920, 790.0 / 1080, 175.0 / 1920, 57.0 / 1080), include=True):
            time.sleep(0.5)
            while True:
                if auto.click_element(("合成", "穿戴"), "text", crop=(959.0 / 1920, 559.0 / 1080, 420.0 / 1920, 395.0 / 1080), include=True):
                    time.sleep(0.5)
                else:
                    break
        self.click_origin()

    def char_db(self) -> dict:
        if not hasattr(self, "_char_db"):
            self._char_db = _load_json(CW_CHAR_DB_PATH, {})
        return self._char_db

    def bond_targets(self) -> dict:
        """还没领的羁绊链路目标 {羁绊: 下一个要达到的层数}。"""
        done = _load_json(CW_BOND_DONE_PATH, {})
        reached = _load_json(CW_BOND_REACHED_PATH, {})
        targets = {}
        for bond, tiers in BOND_LINK_TARGETS.items():
            left = [t for t in tiers if t not in done.get(bond, []) and t not in reached.get(bond, [])]
            if left:
                targets[bond] = min(left)
        return targets

    def owned_bond_counts(self) -> dict:
        """按不同角色名统计已拥有角色（前台/后台/备战席）的羁绊数量。"""
        counts = {}
        names = {c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name}
        for name in names:
            for bond in self.char_db().get(name, {}).get("bonds", []):
                counts[bond] = counts.get(bond, 0) + 1
        return counts

    def _plane(self) -> int:
        try:
            return int(str(self.current_stage).split("-")[0])
        except (ValueError, AttributeError):
            return 1

    def bond_focus(self) -> dict:
        """本局主攻的羁绊链路目标 {羁绊: 层数}（最多 3 个）和对应的一套阵容 self._bond_team。
        羁绊链路要上场 3~7 个不同角色，什么都沾一点只能每样叠两三层；很多角色同时带两三个目标羁绊，
        所以按「一套 ≤9 人的阵容能同时凑齐几个目标」来选：每个目标 +12 分，还缺的角色每点费用 -1、
        每缺一人 -1.5，角色池没余量再扣分；已选的目标有 4 分粘性，免得每回合换方向。"""
        from itertools import combinations
        targets = self.bond_targets()
        db = self.char_db()
        owned = {c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name}
        prev = getattr(self, "_bond_focus", None) or {}
        # 每个目标的分值随位面提高：后期金币多，值得再加一个目标去凑（第四局只选了 1 个目标，2-5 凑齐后 50 金币闲置到结束）
        weight = 16 + 4 * (self._plane() - 1)
        cache_key = (frozenset(owned), tuple(sorted(targets.items())), getattr(self, "_bond_env", None), weight)
        if getattr(self, "_bond_focus_key", None) == cache_key and getattr(self, "_bond_focus", None) is not None:
            return self._bond_focus
        self._bond_focus_key = cache_key
        pools = {}
        for b, t in targets.items():
            pool = [n for n, info in db.items() if b in info.get("bonds", [])]
            if len(pool) >= t:
                pools[b] = pool
        cap = 9

        def plan(bonds):
            left = {b: targets[b] for b in bonds}
            names = {n for b in bonds for n in pools[b]}
            chosen = []
            while any(v > 0 for v in left.values()):
                best = None
                for n in names:
                    if n in chosen:
                        continue
                    cover = sum(1 for b in db[n].get("bonds", []) if left.get(b, 0) > 0)
                    if not cover:
                        continue
                    cost = 0 if n in owned else db[n].get("money", 9)
                    key = (cost / cover, cost, n)
                    if best is None or key < best:
                        best = key
                if best is None or len(chosen) >= cap:
                    return None
                chosen.append(best[2])
                for b in db[best[2]].get("bonds", []):
                    if left.get(b, 0) > 0:
                        left[b] -= 1
            miss = [db[n].get("money", 9) for n in chosen if n not in owned]
            return chosen, sum(miss), len(miss)

        best = None
        cands = sorted(pools)
        for k in (1, 2, 3):
            for bonds in combinations(cands, k):
                res = plan(bonds)
                if not res:
                    continue
                chosen, cost, miss = res
                slack = sum(4 if len(pools[b]) == targets[b] else 2 if len(pools[b]) == targets[b] + 1 else 0 for b in bonds)
                score = weight * k - cost - 1.5 * miss - slack + 4 * len(set(bonds) & set(prev))
                if getattr(self, "_bond_env", None) in bonds:
                    score += 6  # 投资环境/策略选了这个羁绊的加成
                if best is None or score > best[0]:
                    best = (score, bonds, chosen)
        if not best:
            self._bond_focus, self._bond_team = {}, set()
            return {}
        focus = {b: targets[b] for b in best[1]}
        team = set(best[2])
        if focus != prev:
            lack = [n for n in best[2] if n not in owned]
            log.info("羁绊链路：本局主攻 " + "、".join(f"{t}{b}" for b, t in focus.items()) + (f"（还缺 {'、'.join(lack)}）" if lack else "（人齐了）"))
        self._bond_focus, self._bond_team = focus, team
        return focus

    def _want_aura5(self) -> bool:
        """预期收益「通关时阵容中存在 1 名携带主角光环的 5 费角色」还没拿、且本局还没给出去。"""
        if cfg.currencywars_strategy != "default" or getattr(self, "_aura5_done", False):
            return False
        return "aura5" not in _load_json(CW_EXPECT_DONE_PATH, [])

    def _aura5_pick(self) -> Optional[str]:
        """已拥有的、能站前台的 5 费角色（给主角光环用），没有返回 None。"""
        db = self.char_db()
        for c in self.forward_characters + self.backward_characters + self.prepare_characters:
            info = db.get(c.name or "", {})
            if c.name and info.get("money", 0) >= 5 and info.get("pos") in ("forward", "all"):
                return c.name
        return None

    def bond_bonus(self, name: Optional[str], counts: Optional[dict] = None) -> int:
        """羁绊链路加权：在主攻阵容里 +6；带主攻羁绊且该羁绊人还没凑够（可当替补）+3；
        带其他目标羁绊、且这一个就能凑齐时 +3；其余不加。"""
        if not name or cfg.currencywars_strategy != "default":
            return 0
        counts = self.owned_bond_counts() if counts is None else counts
        targets = self.bond_targets()
        # 每次都按当前拥有的角色重算（有缓存）：刚买到/补给拿到的角色要立刻进阵容，不然会被当散件卖掉
        focus = self.bond_focus()
        if name in getattr(self, "_bond_team", ()):
            return 6
        if name == getattr(self, "_aura_char", None):
            return 6  # 带主角光环的角色留在场上
        info5 = self.char_db().get(name, {})
        if info5.get("money", 0) >= 5 and info5.get("pos") in ("forward", "all") and self._want_aura5():
            have5 = self._aura5_pick()
            if have5 is None or have5 == name:
                return 6  # 买一个能站前台的 5 费角色上场，拿「主角光环给 5 费角色」
        real = getattr(self, "_last_bond_list", None) or {}
        bonus = 0
        for bond in self.char_db().get(name, {}).get("bonds", []):
            if bond in focus:
                # 人还没凑够，或者游戏里的真实计数还不够（推算和实际对不上时以实际为准）
                if counts.get(bond, 0) < focus[bond] or real.get(bond, focus[bond]) < focus[bond]:
                    bonus = max(bonus, 3)
            elif bond in targets and counts.get(bond, 0) + 1 >= targets[bond]:
                bonus = max(bonus, 3)
        return bonus

    def read_char_bonds(self) -> list:
        """角色信息面板名字下方那行羁绊标签。"""
        texts = self._get_ocr_texts_in_crop((1400 / 1920, 295 / 1080, 470 / 1920, 50 / 1080))
        return [t.strip() for t in texts if 2 <= len(t.strip()) <= 6]

    def buy_bond_targets(self, keep_money: int = 0):
        """默认策略：商店里有带主攻羁绊（或单个就能凑齐某目标）、且还没拥有的角色就买。
        第一位面守利息线；第二位面可花掉 20 利息；第三位面全花。
        第二位面起主攻只差 1~2 人时按 d 刷新商店找（每次 2 金币）。"""
        if cfg.currencywars_strategy != "default":
            return
        if sum(1 for c in self.prepare_characters if c.name) >= len(self.prepare_pos) - 1:
            self.trim_bench(keep=len(self.prepare_pos) - 2)
            if sum(1 for c in self.prepare_characters if c.name) >= len(self.prepare_pos) - 1:
                return
        focus = self.bond_focus()
        db = self.char_db()
        plane = self._plane()
        money = self.check_money()
        floor = self.interest_floor(money)
        keep = max(keep_money, floor if plane == 1 else max(0, floor - 20) if plane == 2 else 0)
        budget = money - keep
        if budget < 1:
            return
        shop_button_crop = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)

        def owned_names():
            return {c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name}

        def focus_missing(owned):
            miss = 0
            for b, t in focus.items():
                have = sum(1 for n in owned if b in db.get(n, {}).get("bonds", []))
                miss += max(0, t - have)
            # 上一回合出战前读到的真实羁绊列表还差人，也算缺
            real = getattr(self, "_last_bond_list", None) or {}
            real_miss = sum(max(0, t - real[b]) for b, t in focus.items() if b in real)
            return max(miss, real_miss)

        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        bought_any = False
        owned = owned_names()
        counts = self.owned_bond_counts()
        max_refresh = 0 if plane == 1 else (3 if plane == 2 else 6)
        refreshed = 0
        while True:
            texts = self._get_ocr_texts_in_crop(shop_crop)
            cands = []
            for name, info in db.items():
                if name in owned or name not in texts:
                    continue
                score = self.bond_bonus(name, counts)
                cost = info.get("money", 9)
                if score >= 3 and cost <= budget:
                    cands.append((-score, cost, name))
            for _, cost, name in sorted(cands)[:4]:
                if cost > budget:
                    continue
                log.info(f"羁绊链路：购买 {name}（羁绊 {'/'.join(db[name].get('bonds', []))}）")
                if self.try_buy_character(name):
                    budget -= cost
                    bought_any = True
                    owned.add(name)
                    for b in db[name].get("bonds", []):
                        counts[b] = counts.get(b, 0) + 1
            miss = focus_missing(owned)
            # 二位面差 1~3 人、三位面只要还差人就刷（三位面不用留利息，钱不花也是浪费）
            if refreshed >= max_refresh or miss < 1 or miss > (3 if plane < 3 else 9) or budget < 2 + 2:
                break
            auto.press_key('d')
            refreshed += 1
            budget -= 2
            log.info(f"羁绊链路：主攻还差 {miss} 人，刷新商店（第 {refreshed} 次）")
            time.sleep(1)
        # 同一次开店里顺便买上场/主攻角色的重复卡（凑升星），不再单独开关一次商店；同样守利息线
        keep_names = {c.name for c in self.forward_characters + self.backward_characters if c.name} | set(getattr(self, "_bond_team", ()))
        dup_refresh = 0
        while True:
            for _ in range(4):
                texts = self._get_ocr_texts_in_crop(shop_crop)
                aura = getattr(self, "_aura_char", None)  # 带主角光环的角色优先升星
                dup = sorted(((n == aura, db[n].get("money", 9), n) for n in owned if n in keep_names and n in db and n in texts and db[n].get("money", 9) <= budget), reverse=True)
                if not dup:
                    break
                _, cost, name = dup[0]
                log.info(f"购买重复角色 {name}（凑升星）")
                if not self.try_buy_character(name):
                    break
                budget -= cost
                bought_any = True
            # 第三位面主攻已凑齐、金币还多：刷商店买重复卡升星（第六局 3-7 还剩 80 金币没花）。
            # 升星能提高阵容价值，也是「光环给 3 星角色」「6 名 3 星角色」的前提
            if plane < 3 or focus_missing(owned) >= 1 or budget < 12 or dup_refresh >= 8:
                break
            auto.press_key('d')
            dup_refresh += 1
            budget -= 2
            time.sleep(1)
        if dup_refresh:
            log.info(f"主攻已凑齐，刷新商店 {dup_refresh} 次找重复卡升星")
        auto.click_element(shop_button_crop, "crop")
        time.sleep(2)
        self._focus_short = focus_missing(owned) >= 1
        if bought_any:
            self.check_character_status()

    def trim_bench(self, keep: int = 4):
        """默认策略：备战席只留有用的——同名有第二只（凑升星）或对羁绊链路目标加分高的；
        其余没上场的散件按价值从低到高卖掉，备战席最多留 keep 个，换回金币也不再堵「备战席已满」。"""
        if cfg.currencywars_strategy != "default":
            return
        names = [c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name]
        bench = [(i, c) for i, c in enumerate(self.prepare_characters) if c.name]
        if len(bench) <= keep:
            return
        counts = self.owned_bond_counts()
        cands = []
        for i, c in bench:
            if names.count(c.name) > 1 or self.bond_bonus(c.name, counts) >= 3:
                continue
            cands.append(((c.money or 0) + self.bond_bonus(c.name, counts), i))
        cands.sort()
        sold = 0
        for _, i in cands[:len(bench) - keep]:
            name = self.prepare_characters[i].name
            if self._sell_single_character(i):
                log.info(f"清理备战席：出售 {name}")
                self.prepare_characters[i] = CurrencyWarsCharacter(None, None)
                sold += 1
        if sold:
            time.sleep(1)

    def fix_bench_full(self):
        """「备战席已满，请出售角色或提升等级」时出战无效（多出来的角色没地方放，常见于开拓商店买到角色）。
        卖掉备战席最便宜的角色，优先卖没有同名可凑升星的。"""
        warn_crop = (600 / 1920, 510 / 1080, 720 / 1920, 50 / 1080)
        for _ in range(3):
            if not auto.find_element("备战席已满", "text", None, 1, include=True, crop=warn_crop):
                return
            self.click_origin()
            time.sleep(0.5)
            names = [c.name for c in self.forward_characters + self.backward_characters + self.prepare_characters if c.name]
            cands = [(names.count(c.name) > 1, c.money or 0, i) for i, c in enumerate(self.prepare_characters) if c.name]
            if not cands:
                log.warning("备战席已满，但没识别到可出售的角色")
                return
            _, _, idx = min(cands)
            log.info(f"备战席已满，出售 {self.prepare_characters[idx].name}")
            if self._sell_single_character(idx):
                self.prepare_characters[idx] = CurrencyWarsCharacter(None, None)
            time.sleep(1.5)
            self.check_character_status()
            self.deploy_and_optimize()

    def fix_over_limit(self):
        """入画奇航有看不见的单位也占上场名额（如 2-7 出现 9/8），超上限时出战无效会卡死。
        读面板「已上场/上限」，超了就把后台最便宜的角色挪回空的备战席。"""
        limit_crop = (848.0 / 1920, 206.0 / 1080, 231.0 / 1920, 79.0 / 1080)
        for _ in range(3):
            text = auto.get_single_line_text(crop=limit_crop, blacklist=['等级提升', '金币不足']) or ""
            m = re.search(r"(\d+)/(\d+)", text)
            if not m:
                return
            used, cap = m.group(1), int(m.group(2))
            if len(used) >= 2 and used[0] == "1" and int(used) > cap + 3:
                used = used[1:]  # 前面的小人图标常被识别成「1」，如 i8/8 → 18/8
            if int(used) <= cap:
                return
            log.info(f"上场人数超上限（{used}/{cap}），把一名后台角色移回备战席")
            back = [(c.money or 0, i) for i, c in enumerate(self.backward_characters) if c.name]
            empty = [i for i, c in enumerate(self.prepare_characters) if not c.name]
            if not back or not empty:
                log.warning("没有可移动的后台角色或备战席空位")
                return
            _, bi = min(back)
            if not self.move_character(("backward", bi), ("prepare", empty[0])):
                return
            time.sleep(1)

    def _bond_link_swap(self, assigned: dict, max_need: int = 2):
        """羁绊链路按「通关时的羁绊列表」算，只数上场的不同角色。
        某个未完成目标羁绊上场还差 ≤max_need 人、备战席正好有带该羁绊的角色时，
        把上场里最便宜、且换下后不会让已达标目标羁绊掉层的角色换下去。"""
        db = self.char_db()
        targets = self.bond_targets()
        if not targets:
            return

        def bonds_of(item):
            return db.get(item["c"].name, {}).get("bonds", [])

        def counts(items):
            cnt, seen = {}, set()
            for it in items:
                n = it["c"].name
                if n and n not in seen:
                    seen.add(n)
                    for b in bonds_of(it):
                        cnt[b] = cnt.get(b, 0) + 1
            return cnt

        def reached(cnt):
            return {b for b, t in targets.items() if cnt.get(b, 0) >= t}

        for _ in range(3):
            deployed = [(z, i, it) for z in ("forward", "backward") for i, it in enumerate(assigned[z]) if it["c"].name]
            cnt = counts([it for _, _, it in deployed])
            done = reached(cnt)
            on_field = {it["c"].name for _, _, it in deployed}
            bench, seen = [], set()
            for it in assigned["prepare"]:
                n = it["c"].name
                if n and n not in on_field and n not in seen and n in db:
                    seen.add(n)
                    bench.append(it)
            best = None
            for b, t in sorted(targets.items(), key=lambda x: x[1] - cnt.get(x[0], 0)):
                need = t - cnt.get(b, 0)
                if need <= 0 or need > max_need:
                    continue
                ins = sorted([it for it in bench if b in bonds_of(it)], key=lambda it: -(it["c"].money or 0))[:need]
                if len(ins) < need:
                    continue
                # 换下的人：不带 b、换下后已达标的目标不掉，按费用从低到高
                victims = sorted([d for d in deployed if b not in bonds_of(d[2])], key=lambda d: d[2]["c"].money or 0)
                plan, used_v = [], set()
                for inc in ins:
                    pos = inc["c"].pos
                    for v in victims:
                        z, i, it = v
                        if (z, i) in used_v or pos not in (z, "all"):
                            continue
                        used_v.add((z, i))
                        plan.append((z, i, inc))
                        break
                if len(plan) < need:
                    continue
                trial = [it for z, i, it in deployed if (z, i) not in used_v] + [inc for _, _, inc in plan]
                new_done = reached(counts(trial))
                if done <= new_done and len(new_done) > len(done):
                    best = (b, t, plan)
                    break
            if not best:
                return
            b, t, plan = best
            for z, i, inc in plan:
                out = assigned[z][i]
                log.info(f"羁绊链路：{inc['c'].name} 上场替换 {out['c'].name}（凑 {t} {b}）")
                assigned["prepare"] = [x for x in assigned["prepare"] if x is not inc] + [out]
                assigned[z][i] = inc

    def deploy_and_optimize(self):
        """
        部署并自动调整最优站位：
        - forward/backward 按 money 从高到低填充，遵守 pos 限制
        - forward + backward ≤ limit
        - prepare 放剩余角色
        """

        limit = self.check_character_limit()  # 可部署数量

        # 收集所有角色
        all_chars = []
        for zone in ["forward", "backward", "prepare"]:
            chars = self.__dict__[f"{zone}_characters"]
            pos_list = self.__dict__[f"{zone}_pos"]
            for idx, c in enumerate(chars):
                if c.name:
                    all_chars.append({
                        "c": c,
                        "zone": zone,
                        "idx": idx,
                        "pos_ref": pos_list[idx]
                    })
        # 输出 all_chars
        log.debug("所有角色信息：")
        for item in all_chars:
            log.debug(f"角色 {item['c'].name}: 费用={item['c'].money}, 站位={self.pos_name_localization.get(item['c'].pos, item['c'].pos)}, 区域={self.zone_name_localization[item['zone']]}, 索引={item['idx']}")

        source_chars = all_chars
        forward_slots = len(self.forward_characters)
        backward_slots = len(self.backward_characters)

        def _get_effective_pos(item, forced_forward_names=None):
            name = item["c"].name
            if forced_forward_names and name in forced_forward_names:
                return "forward"
            return item["c"].pos

        bond_counts = self.owned_bond_counts()

        def bonus_of(item):
            return self.bond_bonus(item["c"].name, bond_counts)

        def _build_assignment(forced_forward_names=None):
            forced_forward_names = forced_forward_names or set()

            forward_candidates = [item for item in source_chars if _get_effective_pos(item, forced_forward_names) in ("forward", "all")]
            forward_candidates.sort(key=lambda x: (-(x["c"].money + bonus_of(x)), 0 if _get_effective_pos(x, forced_forward_names) == "forward" else 1))
            top_forward: list[dict] = []
            top_forward_names: set[str] = set()
            duplicates_forward: list[dict] = []
            for item in forward_candidates:
                name = item["c"].name
                if name and name not in top_forward_names and len(top_forward) < min(forward_slots, limit):
                    top_forward.append(item)
                    top_forward_names.add(name)
                elif name in top_forward_names:
                    duplicates_forward.append(item)

            pos_priority = {"forward": 0, "backward": 1, "all": 2}
            remaining_pool = [item for item in source_chars if item not in top_forward and item not in duplicates_forward]
            remaining_pool.sort(key=lambda x: (-(x["c"].money + bonus_of(x)), pos_priority.get(_get_effective_pos(x, forced_forward_names), 3)))

            remaining_unique: list[dict] = []
            remaining_names: set[str] = set()
            duplicates_rest: list[dict] = []
            for item in remaining_pool:
                name = item["c"].name
                if name and name not in top_forward_names and name not in remaining_names:
                    remaining_unique.append(item)
                    remaining_names.add(name)
                else:
                    duplicates_rest.append(item)

            ordered_chars = top_forward + remaining_unique + duplicates_forward + duplicates_rest

            log.debug("按投资金额排序后的角色信息：")
            for item in ordered_chars:
                effective_pos = _get_effective_pos(item, forced_forward_names)
                log.debug(f"角色 {item['c'].name}: 费用={item['c'].money}, 站位={self.pos_name_localization.get(effective_pos, effective_pos)}, 区域={self.zone_name_localization[item['zone']]}, 索引={item['idx']}")

            used = 0
            assigned = {"forward": [], "backward": [], "prepare": []}
            used_names = set()

            for item in ordered_chars:
                name = item["c"].name
                effective_pos = _get_effective_pos(item, forced_forward_names)

                if ((effective_pos in ("forward", "all") or len(top_forward) < min(forward_slots, limit)) and
                    len(assigned["forward"]) < forward_slots and
                    used < limit and
                        name not in used_names):
                    assigned["forward"].append(item)
                    used_names.add(name)
                    used += 1
                    continue

                if (effective_pos in ("backward", "all") and
                    len(assigned["backward"]) < backward_slots and
                    used < limit and
                        name not in used_names):
                    assigned["backward"].append(item)
                    used_names.add(name)
                    used += 1
                    continue

                assigned["prepare"].append(item)

            log.debug(
                f"计算区域结果: {self.zone_name_localization['forward']}={len(assigned['forward'])}, {self.zone_name_localization['backward']}={len(assigned['backward'])}, {self.zone_name_localization['prepare']}={len(assigned['prepare'])}, total used={used}/{limit}")
            return ordered_chars, assigned, used

        all_chars, assigned, used = _build_assignment()

        if cfg.currencywars_strategy == "aglaea":
            deployed_names = {
                item["c"].name
                for zone in ("forward", "backward")
                for item in assigned[zone]
                if item["c"].name
            }
            if "风堇" in deployed_names and "藿藿" not in deployed_names:
                # 阿格莱雅策略下检测到风堇已上场但藿藿未上场，强制将风堇视为前台角色后重新排序
                all_chars, assigned, used = _build_assignment({"风堇"})

        elif cfg.currencywars_strategy == "seele":
            # 希儿策略下强制希儿为前台角色
            if self.has_tracked_character("希儿"):
                all_chars, assigned, used = _build_assignment({"希儿"})

        # 无效优化，前台角色放后台不会自动使用技能，保持队伍中有角色空缺即可（好像能凑羁绊 也未必是无效优化，但是我懒得改了^_^）
        # # 补充逻辑：如果 forward 已满且 used < limit，从 prepare 移动角色到 backward
        # if len(assigned['forward']) == min(forward_slots, limit) and used < limit:
        #     log.debug(f"前台已满({len(assigned['forward'])}个)，但总使用量({used})未达上限({limit})，尝试从备战席补充到后台")
        #     # 从 prepare 中找出不重名的角色
        #     to_move = []
        #     for item in assigned["prepare"]:
        #         if used >= limit:
        #             break
        #         name = item["c"].name
        #         if name and name not in used_names:  # 不重名
        #             to_move.append(item)
        #             used_names.add(name)
        #             used += 1

        #     # 移动到 backward 末尾
        #     if to_move:
        #         assigned["prepare"] = [item for item in assigned["prepare"] if item not in to_move]
        #         assigned["backward"].extend(to_move)
        #         log.debug(f"从备战席移动 {len(to_move)} 个角色到后台: {[item['c'].name for item in to_move]}")
        #         log.debug(
        #             f"调整后区域结果: {self.zone_name_localization['forward']}={len(assigned['forward'])}, {self.zone_name_localization['backward']}={len(assigned['backward'])}, {self.zone_name_localization['prepare']}={len(assigned['prepare'])}, total used={used}/{limit}")

        if cfg.currencywars_strategy == "default":
            # 最后一个位面的羁绊列表就是通关结算用的，放宽到差 3 人也凑
            last_plane = str(getattr(self, "current_stage", "") or "").startswith("3-")
            self._bond_link_swap(assigned, max_need=3 if last_plane else 2)

        # 4️⃣ 填充空位
        for zone in ["forward", "backward"]:
            while len(assigned[zone]) < len(self.__dict__[f"{zone}_characters"]):
                assigned[zone].append({"c": CurrencyWarsCharacter(None, None), "zone": "prepare", "idx": None, "pos_ref": None})

        while len(assigned["prepare"]) < len(self.prepare_characters):
            assigned["prepare"].append({"c": CurrencyWarsCharacter(None, None), "zone": "prepare", "idx": None, "pos_ref": None})
        # 显示每个区域的角色
        for zone in ["forward", "backward", "prepare"]:
            names = [item["c"].name for item in assigned[zone]]
            log.debug(f"区域 {self.zone_name_localization[zone]} 角色: {names}")

        # 将已在目标区域的角色提前放置到对应索引，减少后续交换
        for zone in ("forward", "backward"):
            current_zone_list = getattr(self, f"{zone}_characters")
            target_list = assigned[zone]
            for idx, cur_char in enumerate(current_zone_list):
                cur_name = cur_char.name
                if not cur_name:
                    continue
                if target_list[idx]["c"].name == cur_name:
                    continue
                swap_idx = None
                for j in range(len(target_list)):
                    if j == idx:
                        continue
                    if target_list[j]["c"].name == cur_name:
                        swap_idx = j
                        break
                if swap_idx is not None:
                    target_list[idx], target_list[swap_idx] = target_list[swap_idx], target_list[idx]
                    log.debug(f"预先对齐 {self.zone_name_localization[zone]} 索引 {idx}: {cur_name}")

        # 显示每个区域的角色
        for zone in ["forward", "backward", "prepare"]:
            names = [item["c"].name for item in assigned[zone]]
            log.debug(f"区域 {self.zone_name_localization[zone]} 角色: {names}")
        self._log_character_status()

        # 根据计算结果，通过 UI 调用 move_character 来实际交换位置
        # 辅助函数：查找当前角色位置（从目标位置之后开始寻找）
        def _find_current(name: Optional[str], target_zone: Optional[str] = None, target_idx: Optional[int] = None) -> Tuple[Optional[str], Optional[int]]:
            if not name:
                return None, None

            # 构造搜索顺序：从目标位置及之后开始
            zones_order = ["forward", "backward", "prepare"]

            # 如果指定了目标位置，则优先搜索从目标位置之后的位置
            if target_zone is not None:
                target_zone_idx = zones_order.index(target_zone)
                # 重新排列搜索顺序：从目标位置之后开始，然后回到前面
                zones_order = zones_order[target_zone_idx:] + zones_order[:target_zone_idx]

            for z in zones_order:
                lst = getattr(self, f"{z}_characters")

                # 确定起始索引
                start_idx = 0
                if z == target_zone and target_idx is not None:
                    start_idx = target_idx + 1

                # 先搜索从起始索引到末尾
                for idx in range(start_idx, len(lst)):
                    if lst[idx].name == name:
                        return z, idx

                # 如果是目标所在的区域，还要搜索起始索引之前的部分
                if z == target_zone and target_idx is not None:
                    for idx in range(0, target_idx):
                        if lst[idx].name == name:
                            return z, idx

            return None, None

        # 当前已部署数量（forward+backward 非空位计数）
        current_used = sum(1 for c in self.forward_characters if c.name) + sum(1 for c in self.backward_characters if c.name)

        # for zone in ("forward", "backward", "prepare"):
        # prepare 可以跳过排序
        for zone in ("forward", "backward"):
            slot_list = getattr(self, f"{zone}_characters")
            for target_idx in range(len(slot_list)):
                desired = assigned[zone][target_idx]
                desired_name = desired["c"].name

                # 目标应为空，无需移动
                if not desired_name:
                    continue

                # 如果目标位置已经是期望角色则跳过
                cur = slot_list[target_idx]
                if cur.name == desired_name:
                    continue

                # 查找期望角色当前位置
                src_zone, src_idx = _find_current(desired_name, zone, target_idx)
                if src_zone is None:
                    log.error(f"未找到角色 {desired_name} 的当前位置，跳过移动")
                    continue

                target_is_empty = (cur.name is None)
                target_is_forward_backward = (zone in ("forward", "backward"))

                # special case: 源在 prepare，目标为空，但 forward+backward 已达 limit
                if src_zone == "prepare" and target_is_forward_backward and target_is_empty and current_used >= limit:
                    # 在目标位置之后寻找第一个存在的角色作为后备用于交换
                    # 在 forward+backward 两个区间的合并列表中，从目标位置之后寻找第一个存在的角色用于交换
                    f_len = len(self.forward_characters)
                    # b_len = len(self.backward_characters)
                    # 构造合并列表引用
                    combined = list(self.forward_characters) + list(self.backward_characters)
                    # 计算目标在合并列表中的索引
                    if zone == "forward":
                        combined_target_idx = target_idx
                    else:
                        combined_target_idx = f_len + target_idx

                    occ_combined_idx = None
                    # 向后搜索
                    for j in range(combined_target_idx + 1, len(combined)):
                        if combined[j].name:
                            occ_combined_idx = j
                            break

                    # if occ_combined_idx is None:
                    #     for j in range(combined_target_idx - 1, -1, -1):
                    #         if combined[j].name:
                    #             occ_combined_idx = j
                    #             break

                    if occ_combined_idx is None:
                        log.warning(f"没有可用于腾位置的角色，跳过移动 {desired_name}")
                        continue

                    # 将合并索引映射回具体的 zone 和 idx
                    if occ_combined_idx < f_len:
                        occ_zone, occ_idx = "forward", occ_combined_idx
                    else:
                        occ_zone, occ_idx = "backward", occ_combined_idx - f_len

                    # 第一步：把 prepare 中的目标角色与后备已存在的角色交换（可能跨区）
                    if not self.move_character((src_zone, src_idx), (occ_zone, occ_idx)):
                        log.error(f"交换角色失败: {self.zone_name_localization[src_zone]}({src_idx}) <-> {self.zone_name_localization[occ_zone]}({occ_idx})")
                        continue

                    # 重新定位期望角色的位置索引
                    src_zone, src_idx = _find_current(desired_name, zone, target_idx)
                    if src_zone is None:
                        log.error(f"交换后未找到 {desired_name}，跳过")
                        continue

                    # 第二步：将刚放到占位位的角色移动到目标位置（目标为原 zone,target_idx）
                    if not self.move_character((src_zone, src_idx), (zone, target_idx)):
                        log.error(f"交换角色失败: {self.zone_name_localization[src_zone]}({src_idx}) -> {self.zone_name_localization[zone]}({target_idx})")
                        continue

                else:
                    # 一般情况：直接与目标位置交换（无论目标是否为空）
                    if not self.move_character((src_zone, src_idx), (zone, target_idx)):
                        log.error(f"交换角色失败: {self.zone_name_localization[src_zone]}({src_idx}) -> {self.zone_name_localization[zone]}({target_idx})")
                        continue

                # 更新当前已部署计数
                current_used = sum(1 for c in self.forward_characters if c.name) + sum(1 for c in self.backward_characters if c.name)

        self._log_character_status()

        # 检查前台角色数量，如果为0则从后台和备战席查找角色移动到前台
        forward_count = sum(1 for c in self.forward_characters if c.name)
        if forward_count == 0:
            log.info("检测到前台角色为0，尝试从后台和备战席查找角色移动到前台")
            # 先在后台查找
            found = False
            for idx, char in enumerate(self.backward_characters):
                if char.name:
                    log.info(f"从后台找到角色 {char.name}，移动到前台索引0")
                    if self.move_character(("backward", idx), ("forward", 0)):
                        found = True
                        break

            # 如果后台没找到，再在备战席查找
            if not found:
                for idx, char in enumerate(self.prepare_characters):
                    if char.name:
                        log.info(f"从备战席找到角色 {char.name}，移动到前台索引0")
                        if self.move_character(("prepare", idx), ("forward", 0)):
                            break

        if cfg.currencywars_strategy == "aglaea":
            for idx, char in enumerate(self.backward_characters[1:], start=1):
                if char.name == "藿藿":
                    log.info("阿格莱雅策略下检测到藿藿不在后台首位，尝试交换到第一个位置")
                    if not self.move_character(("backward", idx), ("backward", 0)):
                        log.error("藿藿调整到后台首位失败")
                    break

        elif cfg.currencywars_strategy == "seele":
            # 希儿移动到前台时，确保在第一个位置
            if self.has_seele and self.forward_characters and self.forward_characters[0].name != "希儿":
                for idx, char in enumerate(self.forward_characters):
                    if char.name == "希儿":
                        log.info("希儿策略下检测到希儿不在前台首位，尝试交换到第一个位置")
                        if not self.move_character(("forward", idx), ("forward", 0)):
                            log.error("希儿调整到前台首位失败")
                        break

        log.info("角色移动操作完成")

    def _log_character_status(self):
        """
        输出当前各区域的角色状态日志
        """
        log.debug("当前各区域角色状态：")
        for zone in ["forward", "backward", "prepare"]:
            names = [c.name for c in self.__dict__[f"{zone}_characters"]]
            log.debug(f"区域 {self.zone_name_localization[zone]} 角色: {names}")

    def move_character(self, src: tuple, dst: tuple) -> bool:
        """
        在 UI 上把 `src` 位置的角色拖到 `dst` 位置并在内部列表中交换两者。

        参数格式：`(zone, idx)`，其中 `zone` 为 "forward"、"backward" 或 "prepare"，
        `idx` 为该分区内的位置索引（从 0 开始）。

        返回 True 表示成功，False 表示失败（例如未找到 UI 位置）。
        """

        def _validate_and_get(zone_idx):
            if not (isinstance(zone_idx, tuple) and len(zone_idx) == 2 and isinstance(zone_idx[0], str) and isinstance(zone_idx[1], int)):
                raise TypeError("参数必须为 (zone, idx) 形式")
            zone, idx = zone_idx
            if zone not in ("forward", "backward", "prepare"):
                raise ValueError("zone must be 'forward', 'backward' or 'prepare'")
            pos_list = getattr(self, f"{zone}_pos")
            if idx < 0 or idx >= len(pos_list):
                raise IndexError(f"index {idx} out of range for zone {zone}")
            return zone, idx, pos_list[idx]

        z1, i1, crop_a = _validate_and_get(src)
        z2, i2, crop_b = _validate_and_get(dst)

        pos1 = auto.find_element(crop_a, "crop")
        if not pos1:
            log.error("未找到源位置，无法移动")
            return False
        auto.click_element_with_pos(pos1, action="down")
        time.sleep(0.5)

        pos2 = auto.find_element(crop_b, "crop", None, 10)
        if not pos2:
            auto.mouse_up()
            log.error("未找到目标位置，无法移动")
            return False

        auto.click_element_with_pos(pos2, action="move")
        time.sleep(0.5)
        auto.mouse_up()
        time.sleep(0.5)

        # 在内部数据结构中交换角色对象引用
        list1 = getattr(self, f"{z1}_characters")
        list2 = getattr(self, f"{z2}_characters")
        list1[i1], list2[i2] = list2[i2], list1[i1]

        log.info(f"已移动角色: {list2[i2].name}({self.zone_name_localization[z1]}({i1})) <-> {list1[i1].name}({self.zone_name_localization[z2]}({i2}))")
        self._log_character_status()

        # 特殊会弹窗角色
        # 盛会之星角色
        star_characters = {"加拉赫", "大丽花", "花火", "星期日", "知更鸟", "黑天鹅"}
        # 命运卜者角色
        star_characters.add("黑天鹅")
        # 我来当策划角色
        star_characters.add("银狼LV.999")
        # 祈愿试炼角色
        star_characters.update({"远坂凛", "吉尔伽美什", "Saber", "Archer"})
        # 选择伙伴角色
        star_characters.update({"姬子·启行", "丹恒·饮月", "星期日", "瓦尔特", "姬子", "三月七"})
        remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
        if remembrance_trailblazer_name:
            star_characters.add(remembrance_trailblazer_name)

        if list2[i2].name in star_characters or list1[i1].name in star_characters:
            time.sleep(4)  # 等待选择框出现
        self.check_festival_star_popup()
        return True

    def check_festival_star_popup(self):
        """
        检查是否弹出盛会之星或命运卜者等内容的选择框
        """
        for _ in range(5):
            result = auto.get_single_line_text(crop=(936.0 / 1920, 52.0 / 1080, 219.0 / 1920, 53.0 / 1080))
            if result:
                if "盛会之星" in result:
                    log.info("检测到盛会之星")
                    char_crop = (816.0 / 1920, 165.0 / 1080, 222.0 / 1920, 202.0 / 1080)
                    auto.click_element(char_crop, "crop")
                    time.sleep(0.5)
                    auto.click_element("确认选择", "text", crop=(1428.0 / 1920, 539.0 / 1080, 124.0 / 1920, 46.0 / 1080))
                elif "命运卜者" in result:
                    log.info("检测到命运卜者")
                    char_crop = (850.0 / 1920, 167.0 / 1080, 395.0 / 1920, 249.0 / 1080)
                    auto.click_element(char_crop, "crop")
                    time.sleep(0.5)
                    char_crop_pos = [
                        (800.0 / 1920, 372.0 / 1080, 25.0 / 1920, 36.0 / 1080),
                        (1208.0 / 1920, 371.0 / 1080, 25.0 / 1920, 36.0 / 1080),
                        (1617.0 / 1920, 373.0 / 1080, 24.0 / 1920, 35.0 / 1080)
                    ]
                    for pos in char_crop_pos:
                        result = auto.get_single_line_text(crop=pos)
                        if result:
                            # 优先选择0费
                            if result.isdigit() and int(result) == 0:
                                auto.click_element(pos, "crop")
                                time.sleep(0.5)
                                break
                    auto.click_element("确认选择", "text", crop=(1329.0 / 1920, 572.0 / 1080, 332.0 / 1920, 55.0 / 1080))
                elif "我来当策划" in result:
                    log.info("检测到我来当策划")
                    choose_crop = (564 / 1920, 191 / 1080, 449 / 1920, 225 / 1080)
                    auto.click_element(choose_crop, "crop")
                    time.sleep(0.5)
                    auto.click_element("确认选择", "text", crop=(1424 / 1920, 573 / 1080, 134 / 1920, 51 / 1080))
                elif "祈愿试炼" in result:
                    log.info("检测到祈愿试炼")
                    choose1_crop = (458 / 1920, 179 / 1080, 448 / 1920, 339 / 1080)
                    choose2_crop = (1189 / 1920, 187 / 1080, 441 / 1920, 332 / 1080)
                    auto.click_element(choose1_crop, "crop")
                    time.sleep(0.5)
                    auto.click_element("确认选择", "text", crop=(1425 / 1920, 614 / 1080, 138 / 1920, 52 / 1080))
                elif "梦境交织" in result:
                    # 入画奇航：选 N 名角色「共舞」
                    log.info("检测到梦境交织")
                    self.choose_bond_panel_candidates()
                elif "开拓商店" in result:
                    # 入画奇航：开拓银轨羁绊激活后弹出「开拓商店」面板，盖住前台且出战无效。
                    # 首次是卡背（星穹列车，跃迁！），要先点开，羁绊名右侧才会出现收起开关
                    log.info("检测到开拓商店面板，收起")
                    if auto.click_element("跃迁", "text", None, 1, include=True, crop=(900 / 1920, 340 / 1080, 360 / 1920, 80 / 1080)):
                        time.sleep(1.5)
                    auto.click_element((240 / 1920, 245 / 1080, 40 / 1920, 35 / 1080), "crop")
                elif "选择伙伴" in result:
                    log.info("检测到选择伙伴")
                    choose1_crop = (936 / 1920, 165 / 1080, 222 / 1920, 265 / 1080)
                    choose2_crop = (817 / 1920, 166 / 1080, 221 / 1920, 265 / 1080)
                    auto.click_element(choose1_crop, "crop")
                    time.sleep(0.5)
                    auto.click_element(choose2_crop, "crop")
                    time.sleep(0.5)
                    auto.click_element("确认选择", "text", crop=(1423 / 1920, 572 / 1080, 135 / 1920, 50 / 1080))
                elif "奇兽花园" in result:
                    # 入画奇航：选择 1 只奇美拉
                    log.info("检测到奇兽花园")
                    self.choose_bond_panel_candidates(card_offset=0.15)
                time.sleep(2)
                continue
            else:
                break

    def choose_bond_panel_candidates(self, card_offset: float = 0.12):
        """入画奇航羁绊选人面板（如梦境交织、奇兽花园）：按「详情」按钮定位候选卡，依次点上，再确认选择。
        card_offset：卡面在「详情」按钮上方多少（按屏幕高度比例）。"""
        auto.take_screenshot()
        auto.perform_ocr()
        detail_boxes = []
        for box, (text, _conf) in auto.ocr_result:
            if "详情" in text:
                top_left, bottom_right = auto.calculate_text_position(box, False)
                cy = (top_left[1] + bottom_right[1]) / 2
                # 面板在屏幕上半部；备战席里的“详情”不算
                if cy - auto.screenshot_pos[1] < 500 / 1080 * auto.screenshot.height:
                    detail_boxes.append((top_left, bottom_right))
        need = 2
        m = re.search(r"请选择(\d)名", "".join(t for _, (t, _c) in auto.ocr_result))
        if m:
            need = int(m.group(1))
        detail_boxes.sort(key=lambda b: b[0][0])
        h = auto.screenshot.height
        for top_left, bottom_right in detail_boxes[:need]:
            card = ((top_left[0], int(top_left[1] - card_offset * h)), (bottom_right[0], int(bottom_right[1] - card_offset * h)))
            auto.click_element_with_pos(card)
            time.sleep(0.5)
        auto.click_element("确认选择", "text", include=True, crop=(1200 / 1920, 500 / 1080, 500 / 1920, 160 / 1080))
        time.sleep(1)

    def handle_tea_break(self) -> bool:
        """入画奇航：「幕后茶歇」节点没有出战按钮。进去和奇遇伙伴「聊一聊」（赛季指南有对应任务），
        选对话里的选项并确认，领完茶歇奖励后回到备战界面。"""
        if not auto.click_element("幕后茶歇", "text", None, 1, crop=(1580 / 1920, 680 / 1080, 340 / 1920, 120 / 1080), include=True):
            # 茶歇中途切回了备战界面时，入口是右上角「返回幕后茶歇」
            if not auto.click_element("返回幕后茶歇", "text", None, 1, crop=(1560 / 1920, 30 / 1080, 360 / 1920, 80 / 1080), include=True):
                return False
        log.info("检测到幕后茶歇节点")
        time.sleep(3)
        # 首次进入有教学气泡，第一次点击可能只会关掉气泡
        for _ in range(4):
            if auto.click_element("聊一聊", "text", None, 1, crop=(150 / 1920, 780 / 1080, 450 / 1920, 120 / 1080)):
                time.sleep(3)
            if not auto.find_element("聊一聊", "text", None, 1, crop=(150 / 1920, 780 / 1080, 450 / 1920, 120 / 1080)):
                break
        exit_crop = (3.0 / 1920, 37.0 / 1080, 104.0 / 1920, 57.0 / 1080)
        option_confirmed = False
        for _ in range(25):
            if auto.find_element("./assets/images/screen/currency_wars/exit.png", "image", 0.9, crop=exit_crop):
                log.info("幕后茶歇完成")
                return True
            # 点到伙伴头像或选项里带下划线的角色名会弹角色详情（左上是角色详情或事件名，底部有「角色详情」按钮），ESC 关不掉，点右上角 X
            if (auto.find_element("角色详情", "text", None, 1, crop=(90 / 1920, 30 / 1080, 220 / 1920, 45 / 1080), include=True)
                    or auto.find_element("角色详情", "text", None, 1, crop=(760 / 1920, 920 / 1080, 400 / 1920, 80 / 1080), include=True)):
                log.info("幕后茶歇：误入角色详情，返回")
                auto.click_element((1830 / 1920, 35 / 1080, 70 / 1920, 60 / 1080), "crop")
                time.sleep(2)
                continue
            # 选项框在右侧，最后一项是「返回」；选中返回上面那一项后右侧出现「确认」
            if not option_confirmed:
                back = auto.find_element("返回", "text", None, 1, crop=(1000 / 1920, 300 / 1080, 700 / 1920, 600 / 1080))
                if back:
                    # 点选项框右侧的标题条：正文里的角色名带下划线，点到会弹角色详情
                    (bx1, by1), (bx2, by2) = back
                    # back 来自带 crop 的查找，auto.screenshot 只是裁剪图；重新截全图拿窗口位置和尺寸
                    auto.take_screenshot()
                    scale = getattr(auto, "screenshot_scale_factor", 1) or 1
                    left, top = auto.screenshot_pos[0], auto.screenshot_pos[1]
                    h = auto.screenshot.height / scale
                    y_rel = ((by1 + by2) / 2 - top) / h - 0.18
                    auto.click_element((0.85, y_rel - 0.01, 0.06, 0.02), "crop")
                    time.sleep(1)
                    if auto.click_element("确认", "text", None, 3, crop=(1650 / 1920, 300 / 1080, 270 / 1920, 600 / 1080)):
                        log.info("幕后茶歇：已选择伙伴选项")
                        option_confirmed = True
                    time.sleep(2)
                    continue
            if not auto.click_element(("开启", "点击空白处继续"), "text", None, 1, include=True):
                auto.click_element((860 / 1920, 900 / 1080, 200 / 1920, 80 / 1080), "crop")
            time.sleep(2)
        log.warning("幕后茶歇未能回到备战界面")
        return True

    def refresh_protagonist_cards(self, keep_money: int = 10, max_times: int = 6):
        """主角光环卡每次刷新花 1 金币，每次选择最多 6 次。赛季指南有「累计刷新主角光环30次」任务，
        钱够（刷完仍留 keep_money）就左右两张轮流刷新。"""
        holding_crop = (1250 / 1920, 920 / 1080, 180 / 1920, 60 / 1080)
        text = auto.get_single_line_text(crop=holding_crop)
        m = re.search(r"\d+", text or "")
        holding = int(m.group(0)) if m else 0
        log.debug(f"主角光环界面持有金币：{text}")
        times = max(0, min(max_times, holding - keep_money))
        refresh_crops = [(400 / 1920, 750 / 1080, 160 / 1920, 45 / 1080), (1480 / 1920, 750 / 1080, 160 / 1920, 45 / 1080)]
        for i in range(times):
            auto.click_element(refresh_crops[i % 2], "crop")
            time.sleep(1.2)
        if times:
            log.info(f"刷新主角光环 {times} 次")

    def choose_protagonist(self) -> bool:
        """入画奇航：出战按钮变成「选择主角」时，选一张主角光环并把前台第一个角色放进去。"""
        if not auto.click_element("选择主角", "text", None, 1, crop=(1740 / 1920, 709 / 1080, 177 / 1920, 80 / 1080), include=True):
            return False
        log.info("选择主角光环")
        time.sleep(2)
        return self.pick_protagonist_card()

    def reselect_protagonist_guide(self) -> bool:
        """入画奇航 2-1 的「提示：现在你可以重选主角光环」引导会挡住出战，必须走一遍重选主角：
        点左上主角光环框 → 重选主角（花 1 次模式内重选次数）→ 确认 → 重新选光环。"""
        if not auto.find_element("重选主角光", "text", None, 1, include=True, crop=(240 / 1920, 200 / 1080, 500 / 1920, 120 / 1080)):
            return False
        log.info("检测到重选主角光环引导")
        for _ in range(2):  # 切前台后第一下常被吞
            auto.click_element((40 / 1920, 120 / 1080, 170 / 1920, 80 / 1080), "crop")
            time.sleep(1.5)
            if auto.click_element("重选主角", "text", None, 2, include=True, crop=(560 / 1920, 680 / 1080, 400 / 1920, 60 / 1080)):
                break
        time.sleep(1.5)
        auto.click_element("确认", "text", None, 3, crop=(1050 / 1920, 630 / 1080, 300 / 1920, 80 / 1080))
        time.sleep(2.5)
        if not auto.find_element("主角光环", "text", None, 3, include=True, crop=(700 / 1920, 60 / 1080, 520 / 1920, 60 / 1080)):
            log.warning("重选主角光环：没进到选择界面")
            auto.press_key("esc")
            time.sleep(1.5)
            return False
        return self.pick_protagonist_card()

    def reselect_aura_for_5cost(self) -> bool:
        """第三位面前台有 5 费角色、而主角光环还没给过 5 费角色时，主动重选一次主角光环给它
        （左上主角光环框 → 重选主角 → 确认 → 选卡；和 2-1 的重选引导是同一套操作）。每局最多试 2 次。"""
        if not self._want_aura5() or self._plane() < 3 or getattr(self, "_aura5_tried", 0) >= 2:
            return False
        if not any(c.name and (c.money or 0) >= 5 for c in self.forward_characters):
            return False
        self._aura5_tried = getattr(self, "_aura5_tried", 0) + 1
        log.info("预期收益：前台有 5 费角色，重选主角光环给它")
        opened = False
        for _ in range(2):
            auto.click_element((40 / 1920, 120 / 1080, 170 / 1920, 80 / 1080), "crop")
            time.sleep(1.5)
            if auto.click_element("重选主角", "text", None, 2, include=True, crop=(560 / 1920, 680 / 1080, 400 / 1920, 60 / 1080)):
                opened = True
                break
        if not opened:
            log.warning("重选主角光环：没找到「重选主角」按钮（可能没有重选次数了）")
            auto.press_key("esc")
            time.sleep(1)
            self.click_origin()
            return False
        time.sleep(1.5)
        auto.click_element("确认", "text", None, 3, crop=(1050 / 1920, 630 / 1080, 300 / 1920, 80 / 1080))
        time.sleep(2.5)
        if not auto.find_element("主角光环", "text", None, 3, include=True, crop=(700 / 1920, 60 / 1080, 520 / 1920, 60 / 1080)):
            log.warning("重选主角光环：没进到选择界面")
            auto.press_key("esc")
            time.sleep(1.5)
            return False
        return self.pick_protagonist_card()

    def pick_protagonist_card(self) -> bool:
        """在「选择一个角色获得主角光环」界面选一张光环卡并把前台角色拖进去。"""
        self.refresh_protagonist_cards()
        card_crops = [(140 / 1920, 300 / 1080, 440 / 1920, 360 / 1080), (1340 / 1920, 300 / 1080, 440 / 1920, 360 / 1080)]
        front_crops = [(770 / 1920, 580 / 1080, 70 / 1920, 70 / 1080), (876 / 1920, 580 / 1080, 70 / 1920, 70 / 1080),
                       (982 / 1920, 580 / 1080, 70 / 1920, 70 / 1080), (1086 / 1920, 580 / 1080, 70 / 1920, 70 / 1080)]
        slot_crop = (920 / 1920, 420 / 1080, 80 / 1920, 80 / 1080)
        # 预期收益「累计为15名不同的角色选择过主角光环」：没选过的优先，其次费用高（另有「主角光环给5费角色」）
        picked = set(_load_json(CW_PROTAGONIST_PATH, []))
        order = list(range(len(front_crops)))
        fc = self.forward_characters
        # 「15名不同角色」本赛季已领，不再轮换，只按费用（还剩「5费角色」「3星角色」两条）
        rotate = False
        team = getattr(self, "_bond_team", None) or set()

        def aura_key(i):
            c = fc[i] if i < len(fc) else None
            if not c or not c.name:
                return (9, 0)
            if (c.money or 0) >= 5:
                return (0, 0)  # 预期收益「主角光环给 5 费角色」
            if c.name in team:
                return (1, c.money or 0)  # 主攻阵容里最便宜的：会一直留在场上，重复卡好买，冲 3 星（「光环给 3 星角色」）
            return (2, -(c.money or 0))
        order.sort(key=aura_key)
        # 带「敌人难度+」的光环卡放最后
        card_texts = ["".join(self._get_ocr_texts_in_crop(c)) for c in card_crops]
        card_crops = [c for c, t in zip(card_crops, card_texts) if "敌人难度" not in t] + \
                     [c for c, t in zip(card_crops, card_texts) if "敌人难度" in t]
        for card in card_crops:
            auto.click_element(card, "crop")
            time.sleep(1)
            for fi in order:
                front = front_crops[fi]
                src = auto.find_element(front, "crop")
                dst = auto.find_element(slot_crop, "crop")
                if not src or not dst:
                    continue
                auto.click_element_with_pos(src, action="down")
                time.sleep(0.5)
                auto.click_element_with_pos(dst, action="move")
                time.sleep(0.5)
                auto.mouse_up()
                time.sleep(1)
                if not auto.find_element("尚未选择", "text", include=True, crop=(700 / 1920, 860 / 1080, 520 / 1920, 60 / 1080)):
                    if fi < len(fc) and fc[fi].name:
                        log.info(f"主角光环给 {fc[fi].name}")
                        self._aura_char = fc[fi].name
                        if (fc[fi].money or 0) >= 5:
                            self._aura5_done = True
                        if fc[fi].name not in picked:
                            _save_json(CW_PROTAGONIST_PATH, sorted(picked | {fc[fi].name}))
                    auto.click_element("确认", "text", None, 3, crop=(750 / 1920, 920 / 1080, 420 / 1920, 70 / 1080))
                    time.sleep(2)
                    # 角色已有光环时会问「是否替换该角色原有光环？」
                    if auto.find_element("原有光环", "text", None, 1, include=True, crop=(600 / 1920, 470 / 1080, 720 / 1920, 120 / 1080)):
                        auto.click_element("确认", "text", None, 3, crop=(1050 / 1920, 630 / 1080, 300 / 1920, 80 / 1080))
                        time.sleep(2)
                    return True
        log.warning("主角光环没选上，返回备战界面")
        auto.click_element("返回备战界面", "text", include=True)
        time.sleep(2)
        return False

    def identify_current_stage(self):
        """
        识别当前关卡阶段
        """
        stage_crop = (414 / 1920, 56 / 1080, 117 / 1920, 44 / 1080)
        stage_text = auto.get_single_line_text(crop=stage_crop)
        if not (stage_text and re.match(r"^\d-\d$", stage_text)):
            # 入画奇航：开拓银轨的「开拓商店」面板展开时，阶段标签会挪到左上角
            shifted_crop = (270 / 1920, 82 / 1080, 120 / 1920, 48 / 1080)
            stage_text = auto.get_single_line_text(crop=shifted_crop)
        if stage_text and re.match(r"^\d-\d$", stage_text):
            self._unrecognized_stage_count = 0
            log.hr(f"当前阶段：{stage_text}", 2)
            if stage_text == self.current_stage:
                self._stage_unchanged_count += 1
                log.debug(f"当前阶段连续未变化次数：{self._stage_unchanged_count}")
            else:
                self._stage_unchanged_count = 0
            self.current_stage = stage_text
            if self._stage_unchanged_count >= 5:
                log.warning("检测到当前阶段连续5次未发生变化，判定为卡死")
                self.need_exit = True
        else:
            # 入画奇航的羁绊面板打开时顶部标题会移位，先处理弹窗再算未识别
            self.check_festival_star_popup()
            self._unrecognized_stage_count += 1
            log.warning(f"未能识别当前货币战争阶段（连续{self._unrecognized_stage_count}次）")
            if self._unrecognized_stage_count >= 5:
                log.warning("检测到连续5次未能识别阶段，判定为卡死")
                self.need_exit = True
                return
            self._check_equipment_box()
            self._check_hiring_book()
            self._check_expert_invitation()

    def collect_reward(self):
        """
        收集奖励：模拟连续滑动，经过所有奖励图标
        """

        # 通过颜色快速判断一下是否存在奖励可领取（避免每次都滑动一遍）
        if auto.is_rgb_ratio_above_threshold((1306 / 1920, 154 / 1080, 285 / 1920, 329 / 1080), (66, 72, 185), 0.8, tolerance=0.07):
            log.info("没有检测到可领取的奖励，跳过滑动")
            return True
        else:
            log.info("检测到存在可领取的奖励，开始滑动收集")

        reward_pos = [
            (1564 / 1920, 138 / 1080, 26 / 1920, 20 / 1080),
            (1289 / 1920, 138 / 1080, 26 / 1920, 20 / 1080),
            (1291.0 / 1920, 182.0 / 1080, 23.0 / 1920, 21.0 / 1080),
            (1561.0 / 1920, 181.0 / 1080, 23.0 / 1920, 20.0 / 1080),
            (1569.0 / 1920, 225.0 / 1080, 24.0 / 1920, 23.0 / 1080),
            (1297.0 / 1920, 227.0 / 1080, 24.0 / 1920, 22.0 / 1080),
            (1302.0 / 1920, 275.0 / 1080, 25.0 / 1920, 22.0 / 1080),
            (1580.0 / 1920, 274.0 / 1080, 25.0 / 1920, 23.0 / 1080),
            (1588.0 / 1920, 309.0 / 1080, 26.0 / 1920, 21.0 / 1080),
            (1305.0 / 1920, 308.0 / 1080, 27.0 / 1920, 20.0 / 1080),
            (1311.0 / 1920, 343.0 / 1080, 23.0 / 1920, 21.0 / 1080),
            (1596.0 / 1920, 343.0 / 1080, 27.0 / 1920, 23.0 / 1080),
            (1603.0 / 1920, 378.0 / 1080, 25.0 / 1920, 23.0 / 1080),
            (1315.0 / 1920, 377.0 / 1080, 26.0 / 1920, 22.0 / 1080),
            (1320.0 / 1920, 411.0 / 1080, 24.0 / 1920, 24.0 / 1080),
            (1589.0 / 1920, 413.0 / 1080, 27.0 / 1920, 21.0 / 1080),
            (1551.0 / 1920, 449.0 / 1080, 25.0 / 1920, 22.0 / 1080),
            (1324.0 / 1920, 449.0 / 1080, 24.0 / 1920, 23.0 / 1080)
        ]

        # 提取每个区域的中心点（归一化坐标）
        centers = []
        for x, y, w, h in reward_pos:
            cx = x + w / 2
            cy = y + h / 2
            centers.append((cx, cy))

        # 将归一化坐标转为像素坐标
        def to_pixel(pt):
            return (pt[0] * 1920, pt[1] * 1080)

        def to_norm(px, py):
            return (px / 1920, py / 1080)

        pixel_centers = [to_pixel(c) for c in centers]

        # 生成连续路径：根据距离动态确定插值点数
        path_pixels = []
        base_step_distance = 50  # 每50像素一个插值点

        for i in range(len(pixel_centers) - 1):
            x0, y0 = pixel_centers[i]
            x1, y1 = pixel_centers[i + 1]
            # 计算两点间的欧几里得距离
            distance = np.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
            # 根据距离动态计算插值点数，至少2个点
            steps = max(2, int(distance / base_step_distance))
            for t in np.linspace(0, 1, steps, endpoint=False):
                px = x0 + t * (x1 - x0)
                py = y0 + t * (y1 - y0)
                path_pixels.append((px, py))
        # 添加最后一个点
        path_pixels.append(pixel_centers[-1])

        # 转回归一化坐标，并构造“crop”区域
        def make_crop_box(norm_pt):
            return (norm_pt[0], norm_pt[1], 0.001, 0.001)

        # 后台按短段收集，每段都释放，避免整条慢速轨迹超过鼠标保护的两秒时限。
        batch_size = 8 if getattr(auto.input_handler, 'is_background', False) is True else len(path_pixels)
        for offset in range(0, len(path_pixels), batch_size):
            interrupted = False
            try:
                # 从空白处按下，避免装备过多时先拖起装备挡住奖励。
                blank = (6.0 / 1920, 5.0 / 1080, 12.0 / 1920, 14.0 / 1080)
                start = auto.find_element(blank, "crop")
                if not start or auto.click_element_with_pos(start, action="down") is False:
                    interrupted = True
                else:
                    for px, py in path_pixels[offset:offset+batch_size]:
                        crop_box = make_crop_box(to_norm(px, py))
                        auto._debug_clear()
                        pos = auto.find_element(crop_box, "crop", take_screenshot=False)
                        if not pos or auto.click_element_with_pos(pos, action="move") is False:
                            interrupted = True
                            break
            finally:
                # 释放不经过前台入口的暂停卡点，取消/异常时也不能留下按住状态。
                if auto.input_handler.mouse_up() is False:
                    interrupted = True
            if interrupted:
                log.warning("奖励收集手势未完成，已释放鼠标，稍后重新检查当前阶段")
                return False

        # 检查是否弹出 “我来当策划”
        result = auto.get_single_line_text(crop=(972 / 1920, 57 / 1080, 151 / 1920, 44 / 1080))
        if result:
            if "我来当策划" in result:
                log.info("检测到我来当策划")
                choose_crop = (564 / 1920, 191 / 1080, 449 / 1920, 225 / 1080)
                auto.click_element(choose_crop, "crop")
                time.sleep(0.5)
                auto.click_element("确认选择", "text", crop=(1424 / 1920, 573 / 1080, 134 / 1920, 51 / 1080))
                time.sleep(0.5)

        return True

    # 识别等级
    def get_level(self):
        """
        识别当前角色等级
        """
        level_crop = (235.0 / 1920, 876.0 / 1080, 125.0 / 1920, 60.0 / 1080)
        result = auto.get_single_line_text(crop=level_crop)
        if result:
            digits = ''.join(filter(str.isdigit, result))
            if digits.isdigit():
                level = int(digits)
                if 3 <= level <= 10:
                    self.current_level = level
                    return True
        return False

    def check_hp(self):
        """读备战界面右上角小队生命值，读不到返回 None。"""
        text = auto.get_single_line_text(crop=(1415 / 1920, 50 / 1080, 100 / 1920, 50 / 1080)) or ""
        m = re.search(r"\d+", text)
        return int(m.group(0)) if m else None

    def interest_floor(self, money: int) -> int:
        """利息：每持有 10 金币回合结束 +1，最多 +5（50 金币）。前两个位面、血量还够时，
        买经验不把钱花到当前 10 的整数倍以下（最多留 50）；第三位面或血量低时全花。"""
        if cfg.currencywars_strategy != "default":
            return 0
        try:
            plane = int(self.current_stage.split("-")[0])
        except (ValueError, AttributeError):
            plane = 1
        hp = self.check_hp()
        if plane >= 3 or (hp is not None and hp <= 40):
            return 0
        return min(50, money // 10 * 10)

    def buy_experience(self):
        """
        购买经验
        """
        if cfg.currencywars_strategy == "aglaea" and self.current_level == 9:
            log.info("当前等级9，策略为阿格莱雅，跳过购买经验")
            return
        if cfg.currencywars_strategy == "seele":
            if not self.has_seele and self.current_level >= 6:
                log.info("未获得希儿且等级>=6，跳过购买经验")
                return
            elif self.has_seele and not self.has_seele_three_star and self.current_level >= 7:
                log.info("希儿未三星且等级>=7，跳过购买经验")
                return
            elif self.has_seele_three_star and self.current_level >= 9:
                log.info("希儿三星且等级>=9，跳过购买经验")
                return
        if self.current_level == 10:
            log.info("已达最大等级，跳过购买经验")
            return
        previous_money = None
        while True:
            if self.get_level():
                log.debug(f"缓存角色等级信息: 当前等级 {self.current_level}")

            if cfg.currencywars_strategy == "aglaea" and self.current_level == 9:
                log.info("当前等级9，策略为阿格莱雅，跳过购买经验")
                break
            if cfg.currencywars_strategy == "seele":
                if not self.has_seele and self.current_level >= 6:
                    log.info("未获得希儿且等级>=6，停止购买经验")
                    break
                elif self.has_seele and not self.has_seele_three_star and self.current_level >= 7:
                    log.info("希儿未三星且等级>=7，停止购买经验")
                    break
                elif self.has_seele_three_star and self.current_level >= 9:
                    log.info("希儿三星且等级>=9，停止购买经验")
                    break

            money = self.check_money()
            # 要上场的人：主攻阵容 + 带主角光环的角色 + 为「光环给 5 费」留的 5 费角色
            need_slots = len(set(getattr(self, "_bond_team", ())) | {n for n in (getattr(self, "_aura_char", None), self._aura5_pick() if self._want_aura5() else None) if n})
            if cfg.currencywars_strategy == "default" and self.current_level >= max(9, min(10, need_slots)) and money < 60:
                # 9 级已够放下主攻阵容（≤9 人）；升 10 级要花掉四五十金币，留着买角色、刷商店、升星更值
                log.info("已到 9 级，金币留给买角色和刷商店，不再买经验")
                break
            floor = self.interest_floor(money)
            if not floor and cfg.currencywars_strategy == "default" and getattr(self, "_focus_short", False):
                floor = min(money, 6)  # 主攻羁绊还差人：留几块钱下回合刷商店找
            if floor:
                log.info(f"保留 {floor} 金币吃利息")
                money -= floor
            if money >= 4:
                required_buy_times = None
                if cfg.currencywars_type == "normal":
                    need_exp_crop = (235 / 1920, 930 / 1080, 124 / 1920, 38 / 1080)
                    need_exp_text = auto.get_single_line_text(crop=need_exp_crop)
                    if need_exp_text and re.match(r"^\d+/\d+$", need_exp_text):
                        current_exp, total_exp = map(int, need_exp_text.split('/'))
                        need_exp = total_exp - current_exp
                        # 每次购买消耗4货币并提供4经验
                        required_buy_times = (need_exp + 3) // 4
                        affordable_buy_times = money // 4
                        if affordable_buy_times < required_buy_times and cfg.currencywars_strategy == "default" and affordable_buy_times > 0:
                            # 经验会保留；利息线以上的钱不花就是闲置（曾攒到 106 金币没用），先买能买的
                            log.info(f"利息线以上可买经验 {affordable_buy_times} 次（升级需 {required_buy_times} 次），先买")
                            required_buy_times = affordable_buy_times
                        elif affordable_buy_times < required_buy_times:
                            log.info(
                                f"当前经验 {current_exp}，距离升级还需 {need_exp} 经验，"
                                f"需购买 {required_buy_times} 次(可购买 {affordable_buy_times} 次)，货币数量 {money} 不足以购买到升级"
                            )
                            break

                times = required_buy_times if required_buy_times is not None else min(money // 4, 10)
                log.info(f"连续购买经验 {times} 次")
                for _ in range(times):
                    auto.press_key("f")
                    time.sleep(0.3)
                time.sleep(2)

                # 检查货币是否有变化
                if previous_money is not None and money == previous_money:
                    log.info("货币数量未变化，停止购买经验")
                    break
                previous_money = money
            else:
                log.info("停止购买经验")
                break

            if auto.find_element("满级", "text", crop=(250.0 / 1920, 841.0 / 1080, 99.0 / 1920, 40.0 / 1080), include=True):
                self.current_level = 10
                log.info("检测到已满级，停止购买经验")
                break

    def check_money(self):
        """
        检查当前货币数量
        """
        money_crop = (1559.0 / 1920, 880.0 / 1080, 127.0 / 1920, 82.0 / 1080)
        money = auto.get_single_line_text(crop=money_crop, blacklist=['V', '?'])
        if money:
            try:
                money_int = int(money)
                log.info(f"当前货币数量:{money_int}")
                return money_int
            except (ValueError, TypeError):
                log.error(f"货币数量识别失败: {money}")
                return 0
        log.warning("未能识别到货币数量")
        return 0

    def check_character_limit(self):
        '''
        检查当前角色上限
        角色上限 = 当前等级 + 财富宝钻数量 = 面板上限 - 佩佩/叽米等特殊角色数量
        '''
        if self.current_level == 10:
            log.debug(f"已达最大等级，角色上限 {10 + self.diamond_count}")
            return 10 + self.diamond_count

        if self.get_level():
            log.debug(f"解析角色上限信息: 当前等级 {self.current_level}，角色上限 {self.current_level + self.diamond_count}")
            return self.current_level + self.diamond_count

        # 备用方案：直接读取角色上限
        limit_crop = (848.0 / 1920, 206.0 / 1080, 231.0 / 1920, 79.0 / 1080)
        limit = auto.get_single_line_text(crop=limit_crop, blacklist=['等级提升', '金币不足'])
        if limit:
            limit = limit.replace("i", "").replace(":", "")
            limit_parts = limit.split('/')
            if len(limit_parts) == 2 and limit_parts[1].isdigit():
                log.debug(f"备用方案解析角色上限信息: 角色上限 {limit_parts[1]}")
                self.current_level = int(limit_parts[1]) - self.peipei_count - self.diamond_count
                return int(limit_parts[1]) - self.peipei_count

        # 如果都无法解析，尝试获取之前缓存的等级信息
        if self.current_level > 0:
            log.debug(f"使用缓存的等级信息，角色上限 {self.current_level + self.diamond_count}")
            return self.current_level + self.diamond_count

        raise ValueError("无法解析角色上限信息")

    def _check_equipment_box(self):
        """检查并处理武装箱/星徽秘典/阿哈"""
        if not auto.find_element(('武装箱', '星徽秘典', '阿哈'), "text", None, crop=(1012.0 / 1920, 27.0 / 1080, 173.0 / 1920, 56.0 / 1080), include=True):
            return False
        if auto.matched_text == "阿哈":
            log.info(f"检测到为「阿哈」选择装备，尝试点击")
        else:
            log.info(f"检测到{auto.matched_text}，尝试点击")
        if cfg.currencywars_strategy == "aglaea" and self.shoe_count < 4 and auto.click_element(("反重力皮靴", "轮滑鞋"), "text", crop=(535 / 1920, 268 / 1080, 1129 / 1920, 45 / 1080), include=True):
            log.info(f"检测到{auto.matched_text}选项，尝试点击")
        elif cfg.currencywars_strategy == "seele" and not self.allow_seele_equip_weapons:
            # 希儿策略：缺失初级装备优先级最高
            seele_equip_priority = list(self.seele_missing_basic_equips)
            if self.seele_firestorm_count < 2 or self.seele_chainsaw_count < 1:
                if "折叠小刀" not in seele_equip_priority:
                    seele_equip_priority.append("折叠小刀")
            if self.seele_firestorm_count < 2:
                if "轮滑鞋" not in seele_equip_priority:
                    seele_equip_priority.append("轮滑鞋")
            if self.seele_chainsaw_count < 1:
                if "幸运星" not in seele_equip_priority:
                    seele_equip_priority.append("幸运星")
            if self.seele_firestorm_count < 2:
                if "火力风暴潮" not in seele_equip_priority:
                    seele_equip_priority.append("火力风暴潮")
            if self.seele_chainsaw_count < 1:
                if "高周波电锯" not in seele_equip_priority:
                    seele_equip_priority.append("高周波电锯")
            if seele_equip_priority and auto.click_element(tuple(seele_equip_priority), "text", crop=(535 / 1920, 268 / 1080, 1129 / 1920, 45 / 1080)):
                log.info(f"检测到{auto.matched_text}选项，尝试点击")
            else:
                pos = (533.0 / 1920, 135.0 / 1080, 258.0 / 1920, 181.0 / 1080)
                auto.click_element(pos, "crop")
        else:
            pos = (533.0 / 1920, 135.0 / 1080, 258.0 / 1920, 181.0 / 1080)
            auto.click_element(pos, "crop")
        time.sleep(2)
        return True

    def _check_hiring_book(self):
        """检查并处理聘用书"""
        # 聘用书坐标尚未经过测试
        if not auto.find_element("聘用书", "text", None, crop=(923.0 / 1920, 21.0 / 1080, 168.0 / 1920, 74.0 / 1080), include=True):
            return False
        log.info("检测到聘用书选项，尝试点击")
        if cfg.currencywars_strategy == "aglaea":
            remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
            preferred_characters = ["瓦尔特", "昔涟"]
            if remembrance_trailblazer_name:
                preferred_characters.append(remembrance_trailblazer_name)
            preferred_characters.extend(["星期日", "符玄", "银狼", "花火", "风堇", "藿藿", "缇宝"])
            if auto.click_element(tuple(preferred_characters), "text", crop=(501 / 1920, 362 / 1080, 1047 / 1920, 42 / 1080)):
                log.info(f"检测到{auto.matched_text}选项，优先点击")
            else:
                pos = (486.0 / 1920, 159.0 / 1080, 240.0 / 1920, 269.0 / 1080)
                auto.click_element(pos, "crop")
        elif cfg.currencywars_strategy == "seele":
            # 希儿策略：如果没有希儿或希儿没三星，优先希儿；其次按费用从高到低（仅限尚未拥有的角色）
            remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
            preferred_characters = []
            if not self.has_seele or not self.has_seele_three_star:
                preferred_characters.append("希儿")
            all_candidates = ["瓦尔特"]
            if remembrance_trailblazer_name:
                all_candidates.append(remembrance_trailblazer_name)
            all_candidates.extend(["刻律德菈", "符玄", "花火", "佩拉", "风堇", "缇宝"])
            preferred_characters.extend(name for name in all_candidates if not self.has_tracked_character(name))
            if preferred_characters and auto.click_element(tuple(preferred_characters), "text", crop=(501 / 1920, 362 / 1080, 1047 / 1920, 42 / 1080)):
                log.info(f"检测到{auto.matched_text}选项，优先点击")
            else:
                pos = (486.0 / 1920, 159.0 / 1080, 240.0 / 1920, 269.0 / 1080)
                auto.click_element(pos, "crop")
        else:
            pos = (486.0 / 1920, 159.0 / 1080, 240.0 / 1920, 269.0 / 1080)
            auto.click_element(pos, "crop")
        time.sleep(2)
        return True

    def _check_expert_invitation(self):
        """检查并处理专家邀请函"""
        if not auto.find_element("专家邀请函", "text", None, crop=(949 / 1920, 27 / 1080, 153 / 1920, 56 / 1080), include=True):
            return False
        log.info("检测到专家邀请函选项，尝试点击")
        if cfg.currencywars_strategy == "aglaea" and auto.click_element("银狼", "text", crop=(366 / 1920, 363 / 1080, 1318 / 1920, 40 / 1080)):
            pass
        elif cfg.currencywars_strategy == "seele":
            # 希儿策略：如果没有希儿或希儿没三星，优先希儿；其次按费用从高到低（仅限尚未拥有的角色）
            remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
            preferred_characters = []
            if not self.has_seele or not self.has_seele_three_star:
                preferred_characters.append("希儿")
            all_candidates = ["瓦尔特"]
            if remembrance_trailblazer_name:
                all_candidates.append(remembrance_trailblazer_name)
            all_candidates.extend(["刻律德菈", "符玄", "花火", "佩拉", "风堇", "缇宝"])
            preferred_characters.extend(name for name in all_candidates if not self.has_tracked_character(name))
            if preferred_characters and not auto.click_element(tuple(preferred_characters), "text", crop=(366 / 1920, 363 / 1080, 1318 / 1920, 40 / 1080)):
                # 4个
                auto.click_element((769 / 1920, 134 / 1080, 245 / 1920, 276 / 1080), "crop")
                # 5个
                auto.click_element((900 / 1920, 135 / 1080, 249 / 1920, 275 / 1080), "crop")
            pass
        else:
            # 4个
            auto.click_element((769 / 1920, 134 / 1080, 245 / 1920, 276 / 1080), "crop")
            # 5个
            auto.click_element((900 / 1920, 135 / 1080, 249 / 1920, 275 / 1080), "crop")
        time.sleep(2)
        return True

    def check_box(self):
        """
        检查并处理补给阶段的宝箱
        """
        # 滑动完检查一次专家邀请函，疑似没点击开启也会直接弹窗选择界面 (╯▔皿▔)╯
        self._check_expert_invitation()

        # 游戏存在bug，点击开启的速度太快无法弹出选择界面 :(
        res = auto.find_element("开启", "text", None, crop=(376.0 / 1920, 839.0 / 1080, 1125.0 / 1920, 148.0 / 1080), include=True)
        start_time = time.monotonic()
        while res:
            # 检查超时（30秒）
            if time.monotonic() - start_time > 30:
                log.error("开启宝箱操作超时，退出循环")
                break

            log.info("检测到开启按钮，尝试点击")
            auto.click_element_with_pos(res, action="down")
            time.sleep(0.2)
            auto.mouse_up()
            time.sleep(4)

            success = False
            if self._check_equipment_box():
                success = True
            if self._check_hiring_book():
                success = True
            if self._check_expert_invitation():
                success = True
            if not success:
                log.warning("未检测到可点击的选项")
                continue
            res = auto.find_element("开启", "text", None, crop=(376.0 / 1920, 839.0 / 1080, 1125.0 / 1920, 148.0 / 1080), include=True)

    def check_character_status(self):
        """
        检查角色状态
        """
        # 每次检查前重置
        # self.forward_characters = []
        # self.backward_characters = []
        self.prepare_characters = []

        # forward 和 backward 位置检测到空位后，后续默认都为空
        if self.forward_characters == []:
            for pos in self.forward_pos:
                if len(self.forward_characters) > 0 and self.forward_characters[-1].name is None:
                    self.forward_characters.append(CurrencyWarsCharacter(None, None))
                else:
                    self.forward_characters.append(self.check_character_info(pos))

        if self.backward_characters == []:
            for pos in self.backward_pos:
                if len(self.backward_characters) > 0 and self.backward_characters[-1].name is None:
                    self.backward_characters.append(CurrencyWarsCharacter(None, None))
                else:
                    self.backward_characters.append(self.check_character_info(pos))

        # prepare 位置需要全部检测
        for pos in self.prepare_pos:
            self.prepare_characters.append(self.check_character_info(pos, use_rgb_check=True))

    def check_character_info(self, pos: Tuple[float, float, float, float], use_rgb_check: bool = False):
        """
        检查并记录角色信息(使用缓存避免重复识别)
        """
        # 快速判断，减少 OCR 调用
        if use_rgb_check and auto.is_rgb_ratio_above_threshold(pos, (30, 31, 43), 0.8, tolerance=0.01):
            return CurrencyWarsCharacter(None, None)

        char_name_crop = (1493.0 / 1920, 213.0 / 1080, 299.0 / 1920, 30.0 / 1080)
        char_pos_crop = (1494.0 / 1920, 249.0 / 1080, 35.0 / 1920, 40.0 / 1080)
        char_level_crop = (1566.0 / 1920, 161.0 / 1080, 126.0 / 1920, 25.0 / 1080)
        char_money_crop = (1831.0 / 1920, 188.0 / 1080, 35.0 / 1920, 42.0 / 1080)

        auto.click_element(pos, "crop")
        time.sleep(0.5)
        name = auto.get_single_line_text(crop=char_name_crop)

        # 特殊处理：如果识别到的名字是 "M"，则可能是识别错误导致的乱码，暂时视为未识别到角色
        if name == "M":
            name = None

        if name:
            # 检查缓存中是否已有该角色信息
            if name in self.character_info_cache:
                log.info(f"识别到已知角色：{name}")
                if cfg.currencywars_strategy == "aglaea":
                    self.mark_tracked_character(name)
                self.click_origin()
                return self.character_info_cache[name]

            # 首次识别该角色,获取完整信息并缓存
            if auto.find_element("./assets/images/screen/currency_wars/pos_forward.png", "image", 0.9, crop=char_pos_crop):
                cpos = "forward"
            elif auto.find_element("./assets/images/screen/currency_wars/pos_backward.png", "image", 0.9, crop=char_pos_crop):
                cpos = "backward"
            else:
                cpos = "all"
            money = auto.get_single_line_text(crop=char_money_crop)
            if not money or not money.isdigit():
                log.error(f"无法识别角色 {name} 的费用信息，默认为 1")
                money = "1"
            log.info(f"识别到角色：{name}，站位：{self.pos_name_localization[cpos]}，费用：{money}")
            try:
                bonds = self.read_char_bonds()
                if bonds:
                    self.char_db()[name] = {"money": int(money), "pos": cpos, "bonds": bonds}
                    _save_json(CW_CHAR_DB_PATH, self.char_db())
                    log.debug(f"角色 {name} 羁绊：{bonds}")
            except Exception as e:
                log.debug(f"读取角色羁绊失败: {e}")

            if cfg.currencywars_strategy == "aglaea":
                remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
                if name == "阿格莱雅":
                    # 核心角色，视为10费以确保优先上场
                    money = "10"
                    self.mark_tracked_character(name)
                elif name == "风堇":
                    money = "8"
                    cpos = "backward"
                    self.mark_tracked_character(name)
                elif name == "缇宝":
                    money = "9"
                    cpos = "backward"
                    self.mark_tracked_character(name)
                elif name == "藿藿":
                    money = "7"
                    cpos = "backward"
                    self.mark_tracked_character(name)
                elif name == "星期日":
                    self.mark_tracked_character(name)
                elif remembrance_trailblazer_name and name == remembrance_trailblazer_name:
                    cpos = "forward"
                    self.mark_tracked_character(name)
                elif name == "符玄":
                    self.mark_tracked_character(name)
                elif name == "银狼":
                    money = "2"
                    self.mark_tracked_character(name)
                elif name == "花火":
                    cpos = "all"
                    self.mark_tracked_character(name)
                elif name == "瓦尔特":
                    self.mark_tracked_character(name)
                elif name == "昔涟":
                    self.mark_tracked_character(name)

            elif cfg.currencywars_strategy == "seele":
                remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
                if name == "希儿":
                    money = "10"
                    self.has_seele = True
                    self.mark_tracked_character(name)
                elif name == "风堇":
                    money = "9"
                    self.mark_tracked_character(name)
                elif name == "花火":
                    cpos = "all"
                    self.mark_tracked_character(name)
                elif remembrance_trailblazer_name and name == remembrance_trailblazer_name:
                    cpos = "forward"
                    self.mark_tracked_character(name)
                elif name in ("瓦尔特", "刻律德菈", "符玄", "佩拉", "缇宝"):
                    self.mark_tracked_character(name)

            # 创建角色对象并缓存
            character = CurrencyWarsCharacter(name, cpos, money=int(money))
            self.character_info_cache[name] = character

            self.click_origin()
            return character
        else:
            # 部分用户开拓者名字非中英语，导致 OCR 失败，但是角色信息界面仍然打开，需要点击空白处关闭界面否则特定情况下会影响后续操作（比如点进开拓者角色界面）
            self.click_origin()
        return CurrencyWarsCharacter(None, None)

    def click_origin(self):
        """
        点击屏幕原点以取消所有选中状态
        """
        pos = (6.0 / 1920, 5.0 / 1080, 12.0 / 1920, 14.0 / 1080)
        auto.click_element_with_pos(auto.find_element(pos, "crop"))
        time.sleep(0.2)

    def check_auto_battle(self):
        """
        检查并开启自动战斗
        """
        if game_option_enabled(cfg, "auto_battle_detect_enable") and auto.find_element("./assets/images/share/base/not_auto.png", "image", 0.9, crop=(0.0 / 1920, 903.0 / 1080, 144.0 / 1920, 120.0 / 1080)):
            log.info("尝试开启自动战斗")
            auto.press_key(cfg.get_value("hotkey_auto_battle", "v"))
            return True

    def check_click_continue(self):
        """
        检查并点击继续类别的按钮
        """
        if result := auto.find_element(("点击空白处继续", "下一步", "继续挑战", "前往结算", "下一页", "确认选择"), 'text', None, include=True):
            log.info(f"检测到{auto.matched_text}按钮，尝试点击")

            # 某些结算直接通过“前往结算”离开，不会出现“下一页”。
            # 在点击前读取同一帧的结果，避免回到首页后只剩未知结果。
            if auto.matched_text in ("下一页", "前往结算"):
                self._check_battle_result()
                auto.click_element_with_pos(result)
            elif cfg.currencywars_strategy in ("aglaea", "seele") and cfg.currencywars_strategy_restart_on_special_tags:
                if auto.matched_text == "下一步":
                    time.sleep(2)  # 等待BOSS词条加载完成
                    if auto.click_element("下一步", 'text', None, include=True):
                        self._check_boss_tag()
                else:
                    auto.click_element_with_pos(result)
            else:
                auto.click_element_with_pos(result)
            return True

    def _check_boss_tag(self):
        # 沉重脚步：敌人攻击我方队员后，使受到攻击的我方队员行动延后8%。
        # 能量逃逸：敌人受到攻击后，使攻击者能量降低4点，如果能量达到上限则不降低。
        # 忍无可忍：敌人受到7次攻击后，行动提前100%。
        # 同步行动：我方小队获得行动提前效果时，随机使一名精英/首领敌人行动提前20%。
        # 决战在即：首领节点的倒计时减少30，但遭遇节点的倒计时增加20。
        # 战个痛快：战斗节点的倒计时减少30，但首领节点的倒计时增加20。
        # 正当防卫：敌人受到我方前台攻击时，对施放攻击的我方目标造成等同于120%攻击力的伤害，该伤害无法消灭我方目标。
        # 免死金牌：敌人受到攻击期间，若生命值百分比小于1%，使当前生命值变为生命上限的10%，并在本次攻击内不会降低至该值以下，每名敌人只能触发1次该效果。
        # 榜样激励：伤害统计排名第一的队员造成的伤害为原伤害的75%，其他队员造成的伤害为原伤害的110%。
        # 永久创伤：我方小队生命值降低时，会减少等同于生命值降低值20%的生命上限，最多降低生命上限的60%。
        # 变宝为废：每个位面开始时，首次合成的进阶装备会有50%的概率变成垃圾袋。
        # 额外打击：我方队员每有1个空缺装备栏，受到敌人攻击后，额外受到本次攻击伤害8%的真实伤害。
        # 极速制冷：我方前台回合结束时，本回合中每消耗1个战技点，就有15%固定概率陷入冻结状态，持续1回合。
        # 量子熄火：进入战斗时，我方小队中的量子属性目标造成伤害时只能造成1点伤害，施放4次攻击后解除。
        # 坠入陷阱：战斗开始时，我方前台有40%基础概率陷入禁锢、纠缠状态，使其行动延后20%，持续1回合。
        # 忽快忽慢：战斗开始时，速度增幅最高的两名我方角色降低40%速度增幅，最低的两名我方角色提高25%速度增幅。

        # 阿格莱雅策略：直接重开的词条
        aglaea_black_tags = ["沉重脚步", "能量逃逸", "忍无可忍", "同步行动", "决战在即", "战个痛快", "正当防卫", "免死金牌", "榜样激励", "永久创伤", "变宝为废", "额外打击"]
        # 阿格莱雅策略：超过1个才重开的词条
        aglaea_black2_tags = ["雷之熄火", "一鼓作气", "应激反应", "灼热轰炸", "忽快忽慢", "成长的烦恼", "极速制冷", "坠入陷阱"]

        # 希儿策略：直接重开的词条
        seele_black_tags = ["极速制冷", "量子熄火", "免死金牌", "忽快忽慢", "坠入陷阱", "沉重脚步", "忍无可忍", "同步行动", "正当防卫", "榜样激励", "永久创伤", "变宝为废", "额外打击"]
        # 希儿策略：超过1个才重开的词条
        seele_black2_tags = ["一鼓作气", "应激反应", "决战在即", "战个痛快"]

        if cfg.currencywars_strategy == "seele":
            black_tags = seele_black_tags
            black2_tags = seele_black2_tags
            strategy_name = "希儿"
        else:
            black_tags = aglaea_black_tags
            black2_tags = aglaea_black2_tags
            strategy_name = "阿格莱雅"

        # black_tags 直接重开，black2_tags 超过1个才重开
        black2_count = 0
        for box in auto.ocr_result:
            text = box[1][0]
            if text in black_tags:
                log.info(f"检测到{text}词条，当前策略为{strategy_name}，尝试重开")
                self.need_exit = True
                self.need_restart = True
                break
            elif text in black2_tags:
                black2_count += 1
                if black2_count > 1:
                    log.info(f"检测到{text}等词条，当前策略为{strategy_name}，尝试重开")
                    self.need_exit = True
                    self.need_restart = True
                    break

    def _check_battle_result(self):
        """
        检查并记录对局结果
        """
        for box in auto.ocr_result:
            text = box[1][0]
            log.debug(f"检查对局结果文本: {text}")
            if "对局胜利" in text:
                self.result = True
                self.screenshot = auto.screenshot
                return
            elif "对局未完成" in text:
                self.result = False
                self.screenshot = auto.screenshot
                return
            elif "触发晋升赛" in text:
                self.result = True
                self.screenshot = auto.screenshot
                return
            elif "晋升成功" in text:
                self.result = True
                self.screenshot = auto.screenshot
                return

        # 生命值耗尽时可能直接显示“挑战结束 / 前往结算”，随后回到首页。
        # 普通节点的“挑战结束 / 继续挑战”不能当作整局失败。
        texts = {box[1][0] for box in auto.ocr_result}
        if any("挑战结束" in text for text in texts) and any("前往结算" in text for text in texts):
            self.result = False
            self.screenshot = auto.screenshot
            log.info("检测到挑战结束并前往结算，记录本次对局未完成")

    def check_special_characters(self, crop: Optional[Tuple[float, float, float, float]] = None, texts: Optional[list[str]] = None):
        """
        检查指定区域是否有佩佩、叽米或财富宝钻，并更新计数

        参数：
            crop: 要检查的区域坐标，未提供 texts 时会对该区域执行 OCR
            texts: 已有的 OCR 文本结果，提供后直接复用，不再重复 OCR
        """
        if texts is None:
            if crop is None:
                raise ValueError("crop 和 texts 至少需要提供一个")
            texts = self._get_ocr_texts_in_crop(crop)

        # 孪生姵姵：获得一个姵姵，它看起来和你的佩佩一模一样！
        # 佩佩驾到：获得1个携带着3个永久附着星徽的【佩佩】。
        # 这么大的钻石：佩佩携带【财富宝钻】出现
        # 招财狗：获得一个佩佩
        # 溜佩佩+：什么都没带的佩佩会在你的后排逛街
        # 溜佩佩：什么都没带的佩佩会在你的后排逛街
        # 三星佩佩：获得一个穿戴三个随机星徽的佩佩。
        # 粗星佩佩：获得一个穿戴三个随机星徽的佩佩
        # 星徽大使叽米：超稀有的叽米登场！爆出超多星徽！！
        # 金币大使叽米：超稀有的叽米登场！爆出超多金币！！
        if self._find_text_in_texts(texts, ('姵姵', '佩佩', '叽米'), include=True):
            self.peipei_count += 1
            self.update_backward()
        if self._find_text_in_texts(texts, '财富宝钻', include=True):
            self.diamond_count += 1
            # 多元化团队：获得2个【财富宝钻】
            if self._find_text_in_texts(texts, '多元化团队', include=True):
                self.diamond_count += 1
            self.update_backward()

    def check_investment_environment(self):
        """
        检查并执行投资环境/策略操作/遭遇节点
        """
        if auto.find_element(('投资环境', '请选择投资策略', '遭遇节点'), 'text', None, crop=(850.0 / 1920, 59.0 / 1080, 223.0 / 1920, 77.0 / 1080)):
            log.info(f"检测到{auto.matched_text}界面，尝试选择")
            time.sleep(2)
            if auto.matched_text == '遭遇节点':
                button_positions = [
                    (470 / 1920, 346 / 1080, 392 / 1920, 355 / 1080),
                    (1074 / 1920, 357 / 1080, 354 / 1920, 332 / 1080),
                ]
                auto.click_element(button_positions[0], 'crop')
                time.sleep(1)
                auto.click_element('选择', 'text', None, 10, crop=(891 / 1920, 870 / 1080, 381 / 1920, 60 / 1080), include=True)
                return True

            button_positions = [
                (725.0 / 1920, 196.0 / 1080, 468.0 / 1920, 670.0 / 1080),
                (202.0 / 1920, 195.0 / 1080, 470.0 / 1920, 672.0 / 1080),
                (1247.0 / 1920, 198.0 / 1080, 465.0 / 1920, 668.0 / 1080),
            ]
            button_positions_click = [
                (765.0 / 1920, 201.0 / 1080, 394.0 / 1920, 271.0 / 1080),
                (267.0 / 1920, 200.0 / 1080, 384.0 / 1920, 269.0 / 1080),
                (1268.0 / 1920, 204.0 / 1080, 387.0 / 1920, 265.0 / 1080),
            ]
            option_texts = [self._get_ocr_texts_in_crop(pos) for pos in button_positions]
            has_choose = False

            # 投资环境：
            # 昼之半神概念股：开局时获得【昼之半神】角色和初始简易装备，【昼之半神】角色的刷新概率提高。
            # 能量概念股：开局时获得【能量】角色和初始简易装备，【能量】角色的刷新概率提高。
            # 火药味：开局时获得2个简易武装箱。
            # 过剩经费：补给阶段中，角色会携带两件装备。
            # 红钻贵族：开局时拥有【红钻】和一个【简易武装箱】。
            # 蓝钻贵族：开局时拥有【蓝钻】和一个【简易武装箱】。
            # 增发货币：第一/二/三位面开始时，获得一个6/8/12金币的晶矿。
            # 成功经验：在你升到8级后，进入接下来3个节点时，获得12经验。
            # 二手市场：商店刷新20次后，获得30金币和【员工投影仪】。
            # 昼之半神邀请：获得一个【昼之半神星徽】
            # 量子同频邀请：获得一个【量子同频星徽】
            # 能量邀请：获得一个【能量星徽】
            # 特权阶级：第二位面开始时，获得1个特权武装箱。

            # 投资策略：
            # 公司军火更新：每次进入新节点时，你物品栏里的装备变为同品质的其他装备。balabala
            # 超光速提拔：战斗开始时：1个随机的1费角色在这场战斗中升为4星，并获得5%前/后台强度。
            # 自由市场：每当你将要获得一件简易装备时，改为获得1个【简易武装箱】。获得1个随机的简易装备。
            # 优势火力论：获得1个【特权赋予卡】和1个【简易武装箱】。
            # 淘金客：你每次消耗金币刷新商店，都会获得2经验值。

            if cfg.currencywars_strategy == "aglaea":
                white_list = ('昼之半神概念股', '能量概念股', '火药味', '过剩经费', '红钻贵族', '蓝钻贵族', '增发货币', '成功经验', '二手市场', '昼之半神邀请', '量子同频邀请', '能量邀请', '特权阶级')
                white_list += ('公司军火更新', '超光速提拔', '自由市场', '优势火力论', '淘金客')
                for index, texts in enumerate(option_texts):
                    matched_text = self._find_text_in_texts(texts, white_list, include=True)
                    if matched_text:
                        auto.click_element(button_positions_click[index], 'crop')
                        log.info(f"检测到{matched_text}选项，尝试点击")
                        has_choose = True
                        break

            elif cfg.currencywars_strategy == "seele":
                # 希儿策略投资环境优先级（从高到低）
                seele_white_list = ('量子同频契约', '人才储备', '量子同频邀请', '贝洛伯格概念股', '长线利好', '增发货币', '经济过热', '经济严重过热',
                                    '钢铁美学', '量子力学', '枪在手')
                for index, texts in enumerate(option_texts):
                    matched_text = self._find_text_in_texts(texts, seele_white_list, include=True)
                    if matched_text:
                        auto.click_element(button_positions_click[index], 'crop')
                        log.info(f"检测到{matched_text}选项，尝试点击")
                        has_choose = True
                        break

            # 不方便判断的选项，暂时跳过处理
            # 深井角斗场：本局首次达成5连胜时，获得【财富宝钻】。
            # 佩佩客串：进入5个节点后，阿兰接走佩佩们并留下所有装备。
            # 钻石商人：升到9级时，获得【财富宝钻】。
            # 现金为王：出售场上和备战席的所有角色
            # 降本增效：出售场上和备战席的所有角色
            # 大裁员：出售场上和备战席的所有角色
            # 人力重组：移除场上和备战席的所有角色
            # 全员晋升：场上的所有角色会永久升级成比自身高1费的随机角色(最大5费)。获得2个【拆装扳手】。
            # 节省工位：在接下来3个节点只有3个备战席位置
            # 奋斗协议：购买经验值消耗7点小队生命值而非金币
            # 专家研讨会：获得1个【专家邀请函】和1个【简易武装箱】。
            # 快请专家：获得X个【专家邀请函】balabala
            # 英雄登场：获得1个2星5费角色，19个节点后才能将其上场。战斗完胜后，上场节点额外提前1点。
            # 命运礼物：立刻获得一个【惊喜盒】，倒计时12个节点后礼盒打开，战斗完胜后，倒计时额外减少1点。
            # 独家代言：从3件进阶装备中选择一件获取。本局游戏中，你获得组成该装备的简易装备时，改为获得该进阶装备。
            # 全都要：本局游戏中，补给阶段的可选项减少2个。补给阶段选择后，额外获得所有未选择的角色及装备。
            # 广聚天下英才：获得所有2费角色各一个
            # 阿哈大悦：【目前 Wiki 都还没更新这个】
            # 好运来：合成与获得进阶装备时，改为获得一个【好运令牌】。获得2件随机简易装备。

            black_list = ('深井角斗场', '佩佩客串', '钻石商人', '现金为王', '降本增效', '大裁员', '人力重组', '全员晋升', '节省工位', '奋斗协议', '专家研讨会', '快请专家', '英雄登场', '命运礼物', '独家代言', '全都要', '广聚天下英才', '阿哈大悦', '好运来')
            if not has_choose and cfg.currencywars_strategy == "default":
                # 选项文字里带羁绊链路目标羁绊名的（「X概念股」送该羁绊角色并提高刷新率、「X邀请」送星徽）优先，
                # 当前主攻的排最前；选了以后本局主攻往它靠
                tg = self.bond_targets()
                focus = getattr(self, "_bond_focus", None) or {}
                best = None
                for index, texts in enumerate(option_texts):
                    joined = "".join(texts)
                    if not joined.strip() or self._find_text_in_texts(texts, black_list, include=True):
                        continue
                    for b in tg:
                        if b in joined:
                            key = (0 if b in focus else 1, tg[b], index)
                            if best is None or key < best[0]:
                                best = (key, index, b, texts)
                if best:
                    _, index, b, texts = best
                    auto.click_element(button_positions_click[index], 'crop')
                    log.info(f"羁绊链路：选择带「{b}」的选项（第{index + 1}个）")
                    self._bond_env = b
                    has_choose = True
                    self.check_special_characters(texts=texts)

            if not has_choose:
                for index, texts in enumerate(option_texts):
                    matched_black_text = self._find_text_in_texts(texts, black_list, include=True)
                    if matched_black_text:
                        log.debug(f"跳过{matched_black_text}选项")
                        continue
                    if auto.click_element("./assets/images/screen/currency_wars/new.png", "image", 0.9, crop=button_positions[index]):
                        log.info("检测到图鉴未收集选项，尝试点击")
                        has_choose = True
                        self.check_special_characters(texts=texts)
                        break

            if not has_choose:
                for index, texts in enumerate(option_texts):
                    matched_black_text = self._find_text_in_texts(texts, black_list, include=True)
                    if matched_black_text:
                        log.debug(f"跳过{matched_black_text}选项")
                        continue
                    auto.click_element(button_positions_click[index], 'crop')
                    has_choose = True
                    log.info(f"未检测到图鉴未收集选项，选择第{index + 1}个按钮")
                    self.check_special_characters(texts=texts)
                    break

            if not has_choose:
                log.error("所有选项均不可选，尝试退出")
                auto.click_element(button_positions_click[1], 'crop')
                self.need_exit = True
            time.sleep(1)
            auto.click_element('确认', 'text', None, 10, crop=(738.0 / 1920, 927.0 / 1080, 457.0 / 1920, 123.0 / 1080), include=True)
            return True

    def update_backward(self):
        default = 6
        choose = default + self.peipei_count + self.diamond_count
        if choose == 6:
            self.backward_pos = self.backward_pos_6
        elif choose == 7:
            self.backward_pos = self.backward_pos_7[0:7 - self.peipei_count]
        elif choose == 8:
            self.backward_pos = self.backward_pos_8[0:8 - self.peipei_count]
        # 格子最多9个，场上4个佩佩也不会变多
        else:
            self.backward_pos = self.backward_pos_9[0:9 - self.peipei_count]

        # 重置后排角色列表
        self.backward_characters = []

    def check_supply_phase(self):
        """
        检查并执行补给阶段操作
        """
        if auto.find_element(('补给阶段'), 'text', None, crop=(775.0 / 1920, 109.0 / 1080, 368.0 / 1920, 98.0 / 1080)):
            log.info(f"检测到补给阶段界面，尝试选择")
            time.sleep(2)
            button_positions = [
                (262.0 / 1920, 294.0 / 1080, 332.0 / 1920, 487.0 / 1080),
                (616.0 / 1920, 292.0 / 1080, 333.0 / 1920, 489.0 / 1080),
                (971.0 / 1920, 293.0 / 1080, 333.0 / 1920, 488.0 / 1080),
                (1325.0 / 1920, 291.0 / 1080, 333.0 / 1920, 490.0 / 1080),
            ]
            button_positions_click = [
                (297.0 / 1920, 349.0 / 1080, 130.0 / 1920, 241.0 / 1080),
                (651.0 / 1920, 349.0 / 1080, 130.0 / 1920, 242.0 / 1080),
                (1007.0 / 1920, 347.0 / 1080, 130.0 / 1920, 246.0 / 1080),
                (1362.0 / 1920, 346.0 / 1080, 130.0 / 1920, 247.0 / 1080),
            ]
            has_choose = False
            if cfg.currencywars_strategy == "aglaea":
                remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
                preferred_characters = ["瓦尔特", "昔涟"]
                if remembrance_trailblazer_name:
                    preferred_characters.append(remembrance_trailblazer_name)
                preferred_characters.extend(["星期日", "符玄", "银狼", "花火", "风堇", "藿藿", "缇宝"])
                refresh_pos = (1343 / 1920, 959 / 1080, 158 / 1920, 46 / 1080)
                for _ in range(5):
                    if self.shoe_count < 4 and auto.click_element(("反重力皮靴", "轮滑鞋"), "text", crop=(84 / 1920, 620 / 1080, 1749 / 1920, 164 / 1080)):
                        log.info(f"检测到{auto.matched_text}选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    elif self.shoe_count < 4 and not auto.find_element("剩余次数：0", "text", crop=refresh_pos) and not auto.find_element("0", "text", crop=refresh_pos):
                        auto.click_element(refresh_pos, "crop")
                        log.info("刷新补给选项")
                        time.sleep(2)
                    elif not self.has_aglaea_three_star and auto.click_element("阿格莱雅", "text", crop=(87 / 1920, 543 / 1080, 1744 / 1920, 45 / 1080)):
                        log.info("检测到阿格莱雅选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    elif self.has_aglaea_three_star and auto.click_element(tuple(preferred_characters), "text", crop=(87 / 1920, 543 / 1080, 1744 / 1920, 45 / 1080)):
                        log.info(f"检测到{auto.matched_text}选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    elif not auto.find_element("剩余次数：0", "text", crop=refresh_pos) and not auto.find_element("0", "text", crop=refresh_pos):
                        auto.click_element(refresh_pos, "crop")
                        log.info("刷新补给选项")
                        time.sleep(2)
                    else:
                        break

            elif cfg.currencywars_strategy == "seele":
                remembrance_trailblazer_name = self.get_remembrance_trailblazer_name()
                # 策略角色按费用从高到低排列（仅限尚未拥有的角色）
                all_candidates = ["瓦尔特"]
                if remembrance_trailblazer_name:
                    all_candidates.append(remembrance_trailblazer_name)
                all_candidates.extend(["刻律德菈", "符玄", "花火", "佩拉", "风堇", "缇宝"])
                preferred_characters = [name for name in all_candidates if not self.has_tracked_character(name)]
                # 按需构建缺失装备列表
                advanced_equip = []
                if self.seele_firestorm_count < 2:
                    advanced_equip.append("火力风暴潮")
                if self.seele_chainsaw_count < 1:
                    advanced_equip.append("高周波电锯")
                # 缺失初级装备优先级最高
                basic_equip = list(self.seele_missing_basic_equips)
                if self.seele_firestorm_count < 2 or self.seele_chainsaw_count < 1:
                    if "折叠小刀" not in basic_equip:
                        basic_equip.append("折叠小刀")
                if self.seele_firestorm_count < 2:
                    if "轮滑鞋" not in basic_equip:
                        basic_equip.append("轮滑鞋")
                if self.seele_chainsaw_count < 1:
                    if "幸运星" not in basic_equip:
                        basic_equip.append("幸运星")
                refresh_pos = (1343 / 1920, 959 / 1080, 158 / 1920, 46 / 1080)
                supply_text_crop = (84 / 1920, 400 / 1080, 1749 / 1920, 400 / 1080)
                for _ in range(5):
                    # 优先级0：合成时发现缺失的初级装备
                    if self.seele_missing_basic_equips and not self.allow_seele_equip_weapons and auto.click_element(tuple(self.seele_missing_basic_equips), "text", crop=supply_text_crop):
                        log.info(f"检测到{auto.matched_text}缺失初级装备选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    # 优先级1：没有希儿或希儿没三星时，优先希儿
                    if (not self.has_seele or not self.has_seele_three_star) and auto.click_element("希儿", "text", crop=supply_text_crop):
                        log.info("检测到希儿选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    # 优先级2：高级装备（火力风暴潮、高周波电锯）
                    elif not self.allow_seele_equip_weapons and auto.click_element(advanced_equip, "text", crop=supply_text_crop):
                        log.info(f"检测到{auto.matched_text}高级装备选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    # 优先级3：策略角色（按费用从高到低）
                    elif auto.click_element(tuple(preferred_characters), "text", crop=supply_text_crop):
                        log.info(f"检测到{auto.matched_text}选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    # 优先级4：初级装备（用于合成）
                    elif not self.allow_seele_equip_weapons and auto.click_element(basic_equip, "text", crop=supply_text_crop):
                        log.info(f"检测到{auto.matched_text}初级装备选项，尝试点击")
                        has_choose = True
                        time.sleep(1)
                        break
                    # 刷新
                    elif not auto.find_element("剩余次数：0", "text", crop=refresh_pos) and not auto.find_element("0", "text", crop=refresh_pos):
                        auto.click_element(refresh_pos, "crop")
                        log.info("刷新补给选项")
                        time.sleep(2)
                    else:
                        break

            if not has_choose:
                auto.click_element(button_positions_click[2], 'crop', None, 10)
                log.info("默认选择中间补给选项")
                time.sleep(1)
            auto.click_element('确认', 'text', None, 10, crop=(1490.0 / 1920, 943.0 / 1080, 403.0 / 1920, 76.0 / 1080), include=True)
            time.sleep(2)
            return True

    def check_return_home(self) -> bool:
        """
        检查并返回货币战争；已在首页时也应结束本局。
        """
        if not screen.check_screen("currency_wars_homepage"):
            # 结算布局可能变化，按完整按钮文字查找，不限定旧版按钮区域。
            if not auto.click_element('返回货币战争', 'text', None):
                return False
            log.info("检测到返回货币战争按钮，尝试点击")
            time.sleep(3)
            # 等待一段时间后再次检查按钮是否还在
            pos = auto.find_element('返回货币战争', 'text', None)
            if pos:
                log.warning("返回货币战争按钮仍存在，尝试重新点击")
                auto.click_element_with_pos(pos)
                time.sleep(3)
                if auto.find_element('返回货币战争', 'text', None):
                    log.error("无法返回货币战争首页")
                    raise RuntimeError("无法返回货币战争首页")
            screen.wait_for_screen_change("currency_wars_homepage")
        if self.result is not None:
            log.info(f"本次对局结果：{'胜利' if self.result else '失败'}")
            if self.result:
                real = getattr(self, "_last_bond_list", None) or {}
                if real:
                    tg = self.bond_targets()
                    hit = [f"{t}{b}" for b in real for t in BOND_LINK_TARGETS.get(b, []) if t >= tg.get(b, 99) and real[b] >= t]
                    if getattr(self, "_aura5_done", False):
                        log.info("预期收益：本局主角光环给了 5 费角色，应可领")
                        done5 = _load_json(CW_EXPECT_DONE_PATH, [])
                        if "aura5" not in done5:
                            _save_json(CW_EXPECT_DONE_PATH, done5 + ["aura5"])
                    if hit:
                        # 真实羁绊列表和领奖页实测一致，记下来不再追（领奖后挪进 cw_bond_done.json）
                        reached = _load_json(CW_BOND_REACHED_PATH, {})
                        for b in real:
                            for t in BOND_LINK_TARGETS.get(b, []):
                                if t >= tg.get(b, 99) and real[b] >= t and t not in reached.get(b, []):
                                    reached.setdefault(b, []).append(t)
                        _save_json(CW_BOND_REACHED_PATH, reached)
                    log.info("羁绊链路：最后一关前羁绊列表 " + " ".join(f"{b}{n}" for b, n in real.items()) + ("；应可领 " + "、".join(hit) if hit else "；没有新达成的目标"))
        else:
            log.info("本次对局结果：未知")
        log.info("已返回货币战争首页")
        return True
