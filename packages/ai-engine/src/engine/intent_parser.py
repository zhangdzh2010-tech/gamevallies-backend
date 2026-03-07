"""Intent parser for converting natural language to game specifications"""

import re
from typing import Dict, List, Any
from ..api.models import GameSpec, CoreMechanic, GameEntity


class IntentParser:
    """Parses user intent and generates game specifications"""
    
    # Keyword mappings for game type detection
    GAME_TYPE_KEYWORDS = {
        "dodge": ["太空", "飞船", "陨石", "躲避", "避开"],
        "runner": ["跑酷", "奔跑", "跳跃", "滑翔", "跑步"],
        "puzzle": ["拼图", "解谜", "逻辑", "匹配", "连接"],
        "shooter": ["射击", "打靶", "射箭", "炮击", "枪"],
        "rhythm": ["节奏", "音乐", "方块", "跳舞", "同步"],
        "platformer": ["弹跳", "平台", "跳台", "跳板"],
        "snake": ["贪吃蛇", "蛇"],
        "catcher": ["接", "水果", "捕捉", "接住", "篮子"]
    }
    
    # Difficulty keywords
    DIFFICULTY_KEYWORDS = {
        "easy": ["简单", "容易", "新手", "初级"],
        "medium": ["普通", "中等", "标准", "正常"],
        "hard": ["困难", "高难", "挑战", "极难"]
    }
    
    # Visual style keywords
    VISUAL_STYLE_KEYWORDS = {
        "retro": ["复古", "像素", "经典"],
        "modern": ["现代", "简洁", "极简"],
        "colorful": ["彩色", "鲜艳", "多彩"],
        "dark": ["暗黑", "深色", "黑暗"],
        "light": ["亮色", "明亮", "浅色"]
    }
    
    def parse(self, description: str) -> GameSpec:
        """
        Parse natural language description into a GameSpec.
        
        Args:
            description: Natural language game description
            
        Returns:
            GameSpec with detected game type and attributes
        """
        description_lower = description.lower()
        
        # Detect game type
        game_type = self._detect_game_type(description_lower)
        
        # Extract entities
        entities = self._extract_entities(description_lower, game_type)
        
        # Determine core mechanics
        core_mechanics = self._generate_core_mechanics(game_type)
        
        # Determine visual style
        visual_style = self._determine_visual_style(description_lower)
        
        # Set default rules
        rules = self._generate_rules(game_type)
        
        # Detect difficulty
        difficulty = self._detect_difficulty(description_lower)
        
        return GameSpec(
            game_type=game_type,
            core_mechanics=core_mechanics,
            visual_style=visual_style,
            entities=entities,
            rules=rules,
            difficulty_curve=difficulty,
            audio_style="8bit" if "复古" in description or "像素" in description else "modern"
        )
    
    def _detect_game_type(self, description: str) -> str:
        """Detect game type from keywords"""
        # Try to match game type keywords
        for game_type, keywords in self.GAME_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in description:
                    return game_type
        
        # Default to dodge if no match
        return "dodge"
    
    def _extract_entities(self, description: str, game_type: str) -> List[GameEntity]:
        """Extract game entities from description"""
        entities = []
        
        # Default entities based on game type
        default_entities = {
            "dodge": [
                GameEntity(name="player", type="player", behavior="avoids obstacles"),
                GameEntity(name="obstacle", type="obstacle", behavior="falls from top"),
                GameEntity(name="powerup", type="collectible", behavior="grants temporary shield")
            ],
            "runner": [
                GameEntity(name="player", type="player", behavior="jumps over obstacles"),
                GameEntity(name="obstacle", type="obstacle", behavior="moves towards player"),
                GameEntity(name="collectible", type="collectible", behavior="award points")
            ],
            "puzzle": [
                GameEntity(name="piece", type="player", behavior="can be moved and matched"),
                GameEntity(name="board", type="obstacle", behavior="static game board")
            ],
            "shooter": [
                GameEntity(name="player", type="player", behavior="can aim and shoot"),
                GameEntity(name="projectile", type="collectible", behavior="travels towards target"),
                GameEntity(name="enemy", type="obstacle", behavior="shoots back")
            ],
            "rhythm": [
                GameEntity(name="note", type="collectible", behavior="falls in lanes"),
                GameEntity(name="lane", type="obstacle", behavior="tap zone for notes")
            ],
            "platformer": [
                GameEntity(name="player", type="player", behavior="jumps between platforms"),
                GameEntity(name="platform", type="obstacle", behavior="provides jumping surface"),
                GameEntity(name="spike", type="obstacle", behavior="damages player on contact")
            ],
            "snake": [
                GameEntity(name="snake", type="player", behavior="grows when collecting food"),
                GameEntity(name="food", type="collectible", behavior="grows snake by one segment")
            ],
            "catcher": [
                GameEntity(name="basket", type="player", behavior="moves left/right to catch items"),
                GameEntity(name="fruit", type="collectible", behavior="falls from top")
            ]
        }
        
        return default_entities.get(game_type, default_entities["dodge"])
    
    def _generate_core_mechanics(self, game_type: str) -> List[CoreMechanic]:
        """Generate core mechanics based on game type"""
        mechanics_map = {
            "dodge": [
                CoreMechanic(name="movement", description="Move left/right to avoid obstacles"),
                CoreMechanic(name="collision_detection", description="Detect hits with obstacles"),
                CoreMechanic(name="scoring", description="Increase score by surviving")
            ],
            "runner": [
                CoreMechanic(name="jumping", description="Tap to jump over obstacles"),
                CoreMechanic(name="progression", description="Game speed increases over time"),
                CoreMechanic(name="collision_detection", description="Game ends on obstacle hit")
            ],
            "puzzle": [
                CoreMechanic(name="matching", description="Match pieces to clear board"),
                CoreMechanic(name="combo", description="Chain matches for bonus points"),
                CoreMechanic(name="timer", description="Solve puzzle within time limit")
            ],
            "shooter": [
                CoreMechanic(name="aiming", description="Aim and shoot at targets"),
                CoreMechanic(name="ammunition", description="Manage limited ammo"),
                CoreMechanic(name="hit_detection", description="Detect successful hits")
            ],
            "rhythm": [
                CoreMechanic(name="timing", description="Tap notes at the right moment"),
                CoreMechanic(name="combo", description="Build combo for higher score"),
                CoreMechanic(name="feedback", description="Visual/audio feedback on hit")
            ],
            "platformer": [
                CoreMechanic(name="jumping", description="Jump between platforms"),
                CoreMechanic(name="gravity", description="Falling physics"),
                CoreMechanic(name="distance_scoring", description="Score based on distance traveled")
            ],
            "snake": [
                CoreMechanic(name="movement", description="Move in four directions"),
                CoreMechanic(name="growth", description="Body grows when eating food"),
                CoreMechanic(name="self_collision", description="Game ends on self hit")
            ],
            "catcher": [
                CoreMechanic(name="movement", description="Move basket left/right"),
                CoreMechanic(name="catch_detection", description="Detect item catches"),
                CoreMechanic(name="falling_items", description="Items fall from top")
            ]
        }
        
        return mechanics_map.get(game_type, mechanics_map["dodge"])
    
    def _determine_visual_style(self, description: str) -> Dict[str, Any]:
        """Determine visual style from description"""
        style = {
            "color_scheme": "dark",
            "theme": "modern",
            "background_color": "#08080d",
            "primary_color": "#00ff41",
            "secondary_color": "#ff006e",
            "text_color": "#ffffff"
        }
        
        for theme, keywords in self.VISUAL_STYLE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in description:
                    style["theme"] = theme
                    if theme == "retro":
                        style["background_color"] = "#000000"
                        style["primary_color"] = "#0f0"
                    elif theme == "colorful":
                        style["primary_color"] = "#ff00ff"
                    break
        
        return style
    
    def _generate_rules(self, game_type: str) -> Dict[str, Any]:
        """Generate default rules based on game type"""
        rules = {
            "lives": 3,
            "scoring_enabled": True,
            "time_limit": None,
            "level_progression": True,
            "difficulty_scaling": True
        }
        
        if game_type == "puzzle":
            rules["time_limit"] = 300
        elif game_type == "rhythm":
            rules["time_limit"] = 120
        
        return rules
    
    def _detect_difficulty(self, description: str) -> str:
        """Detect difficulty level from description"""
        for difficulty, keywords in self.DIFFICULTY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in description:
                    return difficulty
        
        return "medium"
