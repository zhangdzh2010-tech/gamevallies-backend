"""L1 interaction families and L2 subject×family recipes.

Coverage is by subject × interaction-family, not an exhaustive experiment list.
Templates stay skeleton-only: each family supplies a model step/draw plugin
that the DesktopRuntimeShell hosts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SUBJECTS = ("physics", "chem", "bio")
FAMILY_IDS = (
    "param_formula_panel",
    "time_integrator_1d",
    "field_or_wave_2d",
    "compartment_flow",
    "geometric_ray_2d",
    "bidirectional_converter",
)
CONVERTER_FAMILY_ID = "bidirectional_converter"
M_TO_FT = 3.280839895
# Arcade path is out of scope; stub so the registry has a stable slot.
GAME_FAMILY_STUBS = ("arcade_loop",)


@dataclass(frozen=True)
class Recipe:
    id: str
    family_id: str
    subject: str
    title: str
    formula: str
    keywords: Tuple[str, ...]
    params: Tuple[Dict[str, Any], ...]
    assumptions: str
    limits: str
    units: Dict[str, str] = field(default_factory=dict)
    required_any: Tuple[str, ...] = ()

    def keyword_hits(self, blob: str) -> int:
        if self.required_any and not any(token in blob for token in self.required_any):
            return 0
        return sum(1 for key in self.keywords if key and key in blob)


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    keywords: Tuple[str, ...]
    subjects: Tuple[str, ...]
    interaction: str

    def keyword_hits(self, blob: str) -> int:
        return sum(1 for key in self.keywords if key and key in blob)


def _param(name: str, label: str, minimum: float, maximum: float, step: float, value: float, unit: str = "") -> Dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "min": minimum,
        "max": maximum,
        "step": step,
        "value": value,
        "unit": unit,
    }


FAMILIES: Dict[str, Family] = {
    "param_formula_panel": Family(
        id="param_formula_panel",
        label="参数-公式面板",
        keywords=(
            "定律", "公式", "ohm", "欧姆", "电压", "电阻", "电流",
            "理想气体", "gas law", "pv=nrt", "压强", "体积",
            "酶", "enzyme", "温度", "反应速率", "arrhenius",
            "f=ma", "牛顿", "光合", "photosynthesis", "光照", "二氧化碳",
        ),
        subjects=("physics", "chem", "bio"),
        interaction="live formula from labeled parameters",
    ),
    "time_integrator_1d": Family(
        id="time_integrator_1d",
        label="一维时间积分",
        keywords=(
            "单摆", "pendulum", "摆长", "周期", "自由落体", "free fall",
            "下落", "重力加速度", "抛体", "一维运动", "积分",
        ),
        subjects=("physics",),
        interaction="fixed-step 1d time integration",
    ),
    "field_or_wave_2d": Family(
        id="field_or_wave_2d",
        label="二维场或波动",
        keywords=(
            "波", "干涉", "interference", "波长", "振幅", "波源",
            "波动", "场", "wave", "衍射",
        ),
        subjects=("physics",),
        interaction="2d field or superposed waves",
    ),
    "compartment_flow": Family(
        id="compartment_flow",
        label="隔室流动",
        keywords=(
            "渗透", "osmosis", "隔室", "种群", "捕食", "猎物",
            "lotka", "volterra", "流动", "扩散", "compartment",
        ),
        subjects=("bio", "chem"),
        interaction="exchange or coupled compartment rates",
    ),
    "geometric_ray_2d": Family(
        id="geometric_ray_2d",
        label="几何光线",
        keywords=(
            "平面镜", "反射定律", "入射角", "反射角", "法线",
            "镜面反射", "plane mirror", "reflection law", "geometric optics",
        ),
        subjects=("physics",),
        interaction="labeled angle drives incident and reflected rays",
    ),
    "bidirectional_converter": Family(
        id="bidirectional_converter",
        label="双向换算",
        keywords=(
            "转换器", "换算", "converter", "单位换算", "单位转换",
            "摄氏", "华氏", "英尺", "celsius", "fahrenheit", "米与英尺",
        ),
        subjects=("physics",),
        interaction="bidirectional unit conversion with convert and reset",
    ),
}


RECIPES: Dict[str, Recipe] = {
    "ohm_law": Recipe(
        id="ohm_law",
        family_id="param_formula_panel",
        subject="physics",
        title="欧姆定律",
        formula="I = V / R",
        keywords=("欧姆", "ohm", "电压", "电阻", "电流", "i=v/r", "v/r"),
        params=(
            _param("V", "电压 V", 1, 24, 0.5, 12, "V"),
            _param("R", "电阻 R", 1, 20, 0.5, 6, "Ω"),
        ),
        assumptions="理想电阻，直流，温度不变。",
        limits="不包含电容、电感或非线性元件。",
        units={"V": "V", "R": "Ω", "I": "A"},
        required_any=("欧姆", "ohm", "i=v/r", "v/r", "电阻"),
    ),
    "gas_law": Recipe(
        id="gas_law",
        family_id="param_formula_panel",
        subject="chem",
        title="理想气体状态方程",
        formula="PV = nRT",
        keywords=("理想气体", "gas law", "pv=nrt", "压强", "体积", "摩尔", "气体定律"),
        params=(
            _param("n", "物质的量 n", 0.2, 3, 0.1, 1, "mol"),
            _param("T", "温度 T", 200, 400, 5, 298, "K"),
            _param("V", "体积 V", 0.01, 0.05, 0.001, 0.024, "m³"),
        ),
        assumptions="理想气体，平衡态，R=8.314 J/(mol·K)。",
        limits="高压、低温或真实气体偏差未建模。",
        units={"P": "Pa", "V": "m³", "n": "mol", "T": "K"},
        required_any=("理想气体", "gas law", "pv=nrt", "气体定律", "气体状态"),
    ),
    "enzyme_temp": Recipe(
        id="enzyme_temp",
        family_id="param_formula_panel",
        subject="bio",
        title="酶活性-温度曲线",
        formula="rate = exp[-Ea/R(1/T-1/Tref)] / (1+exp((T-Td)/w))",
        keywords=("酶", "enzyme", "温度", "活性", "变性", "arrhenius", "反应速率"),
        params=(
            _param("T", "温度 T", 0, 80, 1, 37, "°C"),
            _param("Ea", "活化能 Ea", 20, 80, 1, 50, "kJ/mol"),
        ),
        assumptions="相对 37°C 的 Arrhenius 乘以热变性项 D(T)，示意先升后降，不是实验测得的酶活。",
        limits="未区分底物饱和或 pH；示意曲线。",
        units={"T": "°C", "rate": "a.u."},
        required_any=("酶", "enzyme", "arrhenius", "酶活"),
    ),
    "photosynthesis_rate": Recipe(
        id="photosynthesis_rate",
        family_id="param_formula_panel",
        subject="bio",
        title="光合产氧速率",
        formula="rate ∝ I·C / (kI + I) / (kC + C)",
        keywords=("光合", "photosynthesis", "光照", "二氧化碳", "氧气", "产氧"),
        params=(
            _param("I", "光照 I", 0, 100, 1, 40, "%"),
            _param("C", "CO₂ 浓度", 100, 800, 10, 400, "ppm"),
        ),
        assumptions="饱和型示意速率，不是叶片气体交换数据。",
        limits="未建模光抑制、气孔或温度。",
        units={"I": "%", "C": "ppm", "rate": "a.u."},
        required_any=("光合", "photosynthesis", "产氧"),
    ),
    "newtons_second": Recipe(
        id="newtons_second",
        family_id="param_formula_panel",
        subject="physics",
        title="牛顿第二定律",
        formula="a = F / m",
        keywords=("牛顿", "第二定律", "f=ma", "质量", "加速度", "力"),
        params=(
            _param("F", "力 F", 1, 20, 0.5, 10, "N"),
            _param("m", "质量 m", 0.5, 10, 0.5, 2, "kg"),
        ),
        assumptions="质点、合力恒定、无摩擦。",
        limits="不包含转动或变质量。",
        units={"F": "N", "m": "kg", "a": "m/s²"},
        required_any=("牛顿", "f=ma", "第二定律"),
    ),
    "pendulum": Recipe(
        id="pendulum",
        family_id="time_integrator_1d",
        subject="physics",
        title="小角度理想单摆",
        formula="θ'' = -(g/L) sin θ，  T=2π√(L/g)",
        keywords=("单摆", "pendulum", "摆长", "周期", "小角度", "θ", "摆球"),
        params=(
            _param("L", "摆长 L", 0.5, 2.0, 0.1, 1.0, "m"),
            _param("g", "重力 g", 5, 15, 0.1, 9.8, "m/s²"),
            _param("theta0", "初角 θ₀", 0.15, 0.6, 0.05, 0.35, "rad"),
        ),
        assumptions="无阻尼单摆；周期读数用小角度公式。",
        limits="大角度时小角度周期不再准确。",
        units={"L": "m", "g": "m/s²", "T": "s"},
        required_any=("单摆", "pendulum", "摆球", "摆长"),
    ),
    "free_fall": Recipe(
        id="free_fall",
        family_id="time_integrator_1d",
        subject="physics",
        title="自由落体",
        formula="v = gt，  y = y0 - ½gt²",
        keywords=("自由落体", "free fall", "下落", "高度", "v=gt", "重力加速度"),
        params=(
            _param("y0", "高度 y0", 5, 40, 1, 20, "m"),
            _param("g", "重力 g", 5, 15, 0.1, 9.8, "m/s²"),
        ),
        assumptions="无空气阻力，从静止释放。",
        limits="落地后停止积分，不反弹。",
        units={"y": "m", "v": "m/s", "g": "m/s²"},
        required_any=("自由落体", "free fall", "下落"),
    ),
    "wave_interference": Recipe(
        id="wave_interference",
        family_id="field_or_wave_2d",
        subject="physics",
        title="双波源干涉",
        formula="y = A₁sin(kx-ωt) + A₂sin(kx-ωt+φ)",
        keywords=("干涉", "interference", "双波", "波源", "波长", "振幅", "波形"),
        params=(
            _param("A1", "振幅 A1", 0.2, 1.2, 0.1, 0.6, ""),
            _param("A2", "振幅 A2", 0.2, 1.2, 0.1, 0.6, ""),
            _param("lambda", "波长 λ", 40, 140, 5, 80, "px"),
            _param("phi", "相位差 φ", 0.2, 6.2, 0.1, 1.2, "rad"),
        ),
        assumptions="一维投影的两列简谐波线性叠加。",
        limits="不是水槽实验数据；坐标按 2A 留边。",
        units={"A": "a.u.", "lambda": "px"},
        required_any=("干涉", "interference", "双波", "波形"),
    ),
    "mirror_optics": Recipe(
        id="mirror_optics",
        family_id="geometric_ray_2d",
        subject="physics",
        title="平面镜反射",
        formula="θᵢ = θᵣ",
        keywords=(
            "平面镜", "反射定律", "入射角", "入射光", "反射光",
            "镜面", "法线", "反射角", "plane mirror", "reflection",
        ),
        params=(
            _param("theta", "入射角 θ", 10, 70, 1, 30, "°"),
        ),
        assumptions="平面镜，法线垂直于镜面；入射光指向交点，反射光离开交点。",
        limits="几何光学示意，不考虑波动、吸收或镜厚。",
        units={"theta": "°"},
        required_any=("平面镜", "反射定律", "反射光", "plane mirror", "法线", "镜面反射"),
    ),
    "osmosis": Recipe(
        id="osmosis",
        family_id="compartment_flow",
        subject="bio",
        title="渗透水流",
        formula="J = k (c_in - c_out)",
        keywords=("渗透", "osmosis", "半透", "浓度", "隔室", "水流"),
        params=(
            _param("cin", "内侧浓度", 0.1, 1.2, 0.05, 0.8, "mol/L"),
            _param("cout", "外侧浓度", 0.0, 1.0, 0.05, 0.2, "mol/L"),
            _param("k", "渗透系数 k", 0.05, 0.4, 0.05, 0.15, "1/s"),
        ),
        assumptions="两侧充分混合，仅水透过示意膜；水向高浓度侧流动。",
        limits="忽略静水压与真实细胞膜通道。",
        units={"c": "mol/L"},
        required_any=("渗透", "osmosis", "半透"),
    ),
    "population": Recipe(
        id="population",
        family_id="compartment_flow",
        subject="bio",
        title="捕食者-猎物种群",
        formula="x'=x(α-βy)，  y'=y(δx-γ)",
        keywords=("种群", "捕食", "猎物", "lotka", "volterra", "增长率", "捕食率"),
        params=(
            _param("alpha", "猎物增长 α", 0.4, 1.4, 0.1, 0.8, ""),
            _param("beta", "捕食率 β", 0.2, 1.0, 0.05, 0.5, ""),
            _param("gamma", "捕食者死亡 γ", 0.2, 1.0, 0.05, 0.4, ""),
        ),
        assumptions="经典 Lotka–Volterra，连续时间。",
        limits="示意曲线，不是野外计数。",
        units={"x": "prey", "y": "pred"},
        required_any=("种群", "捕食", "猎物", "lotka", "volterra"),
    ),
    "length_m_ft": Recipe(
        id="length_m_ft",
        family_id="bidirectional_converter",
        subject="physics",
        title="米与英尺换算",
        formula="1 m = 3.28084 ft",
        keywords=("米", "英尺", "单位换算", "单位转换", "unit converter", "feet", "meter", "双向"),
        params=(),
        assumptions="标准换算，1 米 = 3.28084 英尺。",
        limits="只处理米与英尺；无效输入用页内提示。",
        units={"m": "m", "ft": "ft"},
        required_any=("米", "英尺", "feet", "meter", "单位换算", "米与英尺"),
    ),
    "temp_c_f": Recipe(
        id="temp_c_f",
        family_id="bidirectional_converter",
        subject="physics",
        title="摄氏华氏换算",
        formula="F = C × 9/5 + 32",
        keywords=("摄氏", "华氏", "温度转换", "温度换算", "celsius", "fahrenheit", "双向"),
        params=(),
        assumptions="线性温标换算，允许负数与小数。",
        limits="只处理摄氏与华氏；无效输入用页内提示。",
        units={"C": "°C", "F": "°F"},
        required_any=("摄氏", "华氏", "celsius", "fahrenheit", "温度转换", "温度换算"),
    ),
}


def get_family(family_id: str) -> Optional[Family]:
    return FAMILIES.get(family_id)


def get_recipe(recipe_id: str) -> Optional[Recipe]:
    return RECIPES.get(recipe_id)


def recipes_for_family(family_id: str) -> List[Recipe]:
    return [recipe for recipe in RECIPES.values() if recipe.family_id == family_id]


def iter_recipes(subject: Optional[str] = None) -> Iterable[Recipe]:
    for recipe in RECIPES.values():
        if subject and recipe.subject != subject:
            continue
        yield recipe


def normalize_brief(text: str) -> str:
    return (text or "").strip().lower()


CONVERTER_UNIT_PAIRS: Dict[str, Tuple[Tuple[str, str], Tuple[str, str]]] = {
    "length_m_ft": (("m", "米"), ("ft", "英尺")),
    "temp_c_f": (("C", "摄氏°C"), ("F", "华氏°F")),
}


def is_converter_family(family_id: str) -> bool:
    return family_id == CONVERTER_FAMILY_ID


def converter_defaults(recipe_id: str) -> Dict[str, str]:
    if recipe_id == "temp_c_f":
        return {"value": "0", "from": "C", "to": "F"}
    return {"value": "1", "from": "m", "to": "ft"}


def convert_bidirectional(recipe_id: str, value: float, from_unit: str, to_unit: str) -> float:
    """Oracle for the assembled converter plugin."""
    if from_unit == to_unit:
        return float(value)
    if recipe_id == "temp_c_f":
        if from_unit == "C" and to_unit == "F":
            return float(value) * 9.0 / 5.0 + 32.0
        if from_unit == "F" and to_unit == "C":
            return (float(value) - 32.0) * 5.0 / 9.0
    else:
        if from_unit == "m" and to_unit == "ft":
            return float(value) * M_TO_FT
        if from_unit == "ft" and to_unit == "m":
            return float(value) / M_TO_FT
    raise ValueError(f"unsupported conversion {recipe_id}:{from_unit}->{to_unit}")


def _escape_attr(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_param_controls(params: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for spec in params:
        name = spec["name"]
        parts.append(
            '<label>{label}<input data-work-param="{name}" id="param-{name}" '
            'type="range" min="{min}" max="{max}" step="{step}" value="{value}">'
            '</label>'.format(
                label=spec["label"],
                name=name,
                min=spec["min"],
                max=spec["max"],
                step=spec["step"],
                value=spec["value"],
            )
        )
    return "".join(parts)


def render_converter_controls(recipe: Recipe) -> str:
    units = CONVERTER_UNIT_PAIRS.get(recipe.id, CONVERTER_UNIT_PAIRS["length_m_ft"])
    defaults = converter_defaults(recipe.id)

    def options(selected: str) -> str:
        return "".join(
            '<option value="{code}"{sel}>{label}</option>'.format(
                code=_escape_attr(code),
                sel=" selected" if code == selected else "",
                label=_escape_attr(label),
            )
            for code, label in units
        )

    return (
        '<label>数值 <input id="valueInput" name="value" type="number" step="any" '
        'value="{value}" aria-label="换算数值"></label>'
        '<label>从 <select id="fromUnit" aria-label="原单位">{from_opts}</select></label>'
        '<label>到 <select id="toUnit" aria-label="目标单位">{to_opts}</select></label>'
        '<button type="button" id="convertBtn">转换</button>'
        '<button type="button" id="resetBtn">重置</button>'
        '<output id="convertResult" data-work-output></output>'
        '<p id="convertHint" data-work-secondary hidden></p>'
    ).format(
        value=_escape_attr(defaults["value"]),
        from_opts=options(defaults["from"]),
        to_opts=options(defaults["to"]),
    )


_WORK_TOKENS_JS = r"""
    function workTokens(){
      var cs = (document.body && window.getComputedStyle) ? getComputedStyle(document.body) : null;
      function read(name, fallback){
        var v = cs ? String(cs.getPropertyValue(name) || '').trim() : '';
        return v || fallback;
      }
      return {
        bg: read('--work-canvas-bg', '#f1f5f9'),
        ink: read('--work-ink', '#0f172a'),
        accent: read('--work-accent', '#0f766e'),
        muted: read('--work-muted', '#475569'),
        line: read('--work-line', '#94a3b8'),
        warn: read('--work-warn', '#c2410c'),
        surface: read('--work-surface', '#ffffff')
      };
    }
"""


def family_plugin_js(family_id: str, recipe: Recipe) -> str:
    """Deterministic model plugin. Presentation copy is filled around it."""
    plugins = {
        "param_formula_panel": _PARAM_FORMULA_JS,
        "time_integrator_1d": _TIME_INTEGRATOR_JS,
        "field_or_wave_2d": _WAVE_JS,
        "compartment_flow": _COMPARTMENT_JS,
        "geometric_ray_2d": _RAY_OPTICS_JS,
        "bidirectional_converter": _CONVERTER_JS,
    }
    template = plugins.get(family_id, _PARAM_FORMULA_JS)
    script = template.replace("__RECIPE__", recipe.id)
    if family_id != CONVERTER_FAMILY_ID:
        script = script.replace(
            "window.WorkFamily = (function(){",
            "window.WorkFamily = (function(){" + _WORK_TOKENS_JS,
            1,
        )
    return script


def wave_superposition(x: float, t: float, *, amplitude1: float, amplitude2: float, wavelength: float, phase: float) -> float:
    """y = A1 sin(kx - t) + A2 sin(kx - t + φ). Phase is only on the second source."""
    lam = max(float(wavelength), 1e-6)
    k = 2.0 * math.pi / lam
    return (
        float(amplitude1) * math.sin(k * x - t)
        + float(amplitude2) * math.sin(k * x - t + float(phase))
    )


def plane_mirror_rays(theta_rad: float, *, hit: Tuple[float, float] = (0.0, 0.0), length: float = 1.0) -> Dict[str, Tuple[float, float]]:
    """Vertical mirror, normal along -x. Incident travels toward hit; reflected leaves hit."""
    mx, cy = hit
    length = max(float(length), 1e-6)
    theta = max(0.0, min(math.pi / 2 - 1e-3, float(theta_rad)))
    incident_from = (mx - length * math.cos(theta), cy - length * math.sin(theta))
    reflected_to = (mx - length * math.cos(theta), cy + length * math.sin(theta))
    return {
        "incident_from": incident_from,
        "hit": (mx, cy),
        "reflected_to": reflected_to,
        "normal_to": (mx - length, cy),
    }


# Relative Arrhenius × logistic denaturation. Tref=37°C so A(37°C)=1.
# Td=48°C, w=4°C: default Ea=50 kJ/mol peaks inside 37–50°C and falls by 80°C.
ENZYME_GAS_R = 8.314
ENZYME_TREF_C = 37.0
ENZYME_TDENATURE_C = 48.0
ENZYME_DENATURE_WIDTH_C = 4.0
ENZYME_DEFAULT_EA_KJ = 50.0
ENZYME_TEMP_MIN_C = 0.0
ENZYME_TEMP_MAX_C = 80.0

OSMOSIS_VOL_MIN = 0.15
OSMOSIS_VOL_MAX = 0.85
OSMOSIS_START_VIN = 0.32
OSMOSIS_START_VOUT = 0.58
OSMOSIS_FLUX_SCALE = 1.2

PHOTOSYNTHESIS_K_LIGHT = 20.0
PHOTOSYNTHESIS_K_CO2 = 200.0


def photosynthesis_oxygen_rate(light_pct: float, co2_ppm: float) -> float:
    """Saturating schematic: rate ∝ I/(kI+I) · C/(kC+C)."""
    light = max(0.0, float(light_pct))
    co2 = max(0.0, float(co2_ppm))
    return (light / (PHOTOSYNTHESIS_K_LIGHT + light)) * (co2 / (PHOTOSYNTHESIS_K_CO2 + co2))


def enzyme_activity_rate(temp_c: float, ea_kj_mol: float = ENZYME_DEFAULT_EA_KJ) -> float:
    """rate = exp(-Ea/R·(1/T-1/Tref)) / (1+exp((T-Td)/w))."""
    tk = float(temp_c) + 273.15
    tref = ENZYME_TREF_C + 273.15
    ea = float(ea_kj_mol) * 1000.0
    arrhenius = math.exp(-ea / ENZYME_GAS_R * (1.0 / tk - 1.0 / tref))
    denature = 1.0 / (1.0 + math.exp((float(temp_c) - ENZYME_TDENATURE_C) / ENZYME_DENATURE_WIDTH_C))
    return arrhenius * denature


def enzyme_activity_peak_celsius(
    ea_kj_mol: float = ENZYME_DEFAULT_EA_KJ,
    t_min: float = ENZYME_TEMP_MIN_C,
    t_max: float = ENZYME_TEMP_MAX_C,
    step: float = 0.25,
) -> Tuple[float, float]:
    """Interior temperature of the maximum rate on [t_min, t_max]."""
    best_t = t_min
    best_rate = -1.0
    sample = t_min
    while sample <= t_max + 1e-9:
        rate = enzyme_activity_rate(sample, ea_kj_mol)
        if rate > best_rate:
            best_t = sample
            best_rate = rate
        sample += step
    return best_t, best_rate


def osmosis_volume_step(
    vin: float,
    vout: float,
    cin: float,
    cout: float,
    k: float,
    dt: float,
    *,
    scale: float = OSMOSIS_FLUX_SCALE,
) -> Tuple[float, float, float]:
    """Water flux J=k(cin-cout) toward the high-concentration side."""
    flux = float(k) * (float(cin) - float(cout))
    transfer = flux * float(dt) * float(scale)
    next_in = min(OSMOSIS_VOL_MAX, max(OSMOSIS_VOL_MIN, float(vin) + transfer))
    next_out = min(OSMOSIS_VOL_MAX, max(OSMOSIS_VOL_MIN, float(vout) - transfer))
    return next_in, next_out, flux


_PARAM_FORMULA_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var phase = 0.2;
    var oxygen = 0.15;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function enzymeRate(Tc, Ea){
      var Tk = Tc + 273.15;
      var Tref = 37 + 273.15;
      var arr = Math.exp(-Ea * 1000 / 8.314 * (1 / Tk - 1 / Tref));
      var denature = 1 / (1 + Math.exp((Tc - 48) / 4));
      return arr * denature;
    }
    function compute(){
      if (recipe === 'ohm_law'){
        var V = num('param-V', 12), R = Math.max(0.01, num('param-R', 6));
        var I = V / R;
        setReadout('I=' + I.toFixed(3) + ' A');
        return {kind:'ohm', V:V, R:R, I:I};
      }
      if (recipe === 'gas_law'){
        var n = num('param-n', 1), T = num('param-T', 298), vol = Math.max(0.001, num('param-V', 0.024));
        var P = n * 8.314 * T / vol;
        var nRT = n * 8.314 * T;
        setReadout('P=' + P.toFixed(0) + ' Pa  PV=' + (P*vol).toFixed(0) + '  nRT=' + nRT.toFixed(0));
        return {kind:'gas', n:n, T:T, V:vol, P:P};
      }
      if (recipe === 'enzyme_temp'){
        var Tc = num('param-T', 37), Ea = num('param-Ea', 50);
        var rate = enzymeRate(Tc, Ea);
        setReadout('rate=' + rate.toFixed(3) + ' a.u.  T=' + Tc.toFixed(0) + '°C');
        return {kind:'enzyme', T:Tc, Ea:Ea, rate:rate};
      }
      if (recipe === 'photosynthesis_rate'){
        var I = num('param-I', 40), C = num('param-C', 400);
        var rate = (I / (20 + I)) * (C / (200 + C));
        setReadout('O₂ rate=' + rate.toFixed(3) + ' a.u.  t=' + phase.toFixed(2) + ' s  ΣO₂=' + oxygen.toFixed(2));
        return {kind:'photo', I:I, C:C, rate:rate};
      }
      var F = num('param-F', 10), m = Math.max(0.1, num('param-m', 2));
      var a = F / m;
      setReadout('a=' + a.toFixed(3) + ' m/s²');
      return {kind:'newton', F:F, m:m, a:a};
    }
    return {
      applyParams: compute,
      onStart: function(){
        if (phase < 0.05) phase = 0.2;
        if (recipe === 'photosynthesis_rate' && oxygen < 0.05) oxygen = 0.15;
        compute();
      },
      reset: function(){
        phase = 0.2;
        oxygen = 0.15;
        compute();
      },
      step: function(dt){
        phase += dt;
        if (recipe === 'photosynthesis_rate'){
          oxygen += compute().rate * dt;
          return;
        }
        if (recipe === 'enzyme_temp') compute();
      },
      draw: function(ctx, canvas){
        if (!ctx || !canvas || typeof ctx.clearRect !== 'function') return;
        var s = compute();
        var w = canvas.width || 0, h = canvas.height || 0;
        if (w < 8 || h < 8) return;
        ctx.clearRect(0,0,w,h);
        var t = workTokens();
        ctx.fillStyle = t.bg; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = t.accent; ctx.lineWidth = 2;
        if (s.kind === 'ohm'){
          ctx.strokeRect(w*0.2, h*0.35, w*0.6, h*0.3);
          var pulse = 0.3 + 0.2 * Math.sin(phase * s.I);
          ctx.fillStyle = t.accent;
          ctx.beginPath(); ctx.arc(w*(0.25+pulse), h*0.5, 8, 0, Math.PI*2); ctx.fill();
        } else if (s.kind === 'gas'){
          var vNorm = Math.min(1, Math.max(0, (s.V - 0.01) / 0.04));
          var chamberH = h * (0.32 + 0.48 * vNorm);
          var chamberW = w * 0.26;
          var left = w * 0.08;
          var top = h - 14 - chamberH;
          ctx.strokeStyle = t.line;
          ctx.strokeRect(left, top, chamberW, chamberH);
          ctx.fillStyle = t.muted;
          ctx.fillRect(left - 6, top - 8, chamberW + 12, 10);
          var count = Math.max(5, Math.round(s.n * 8));
          var speed = Math.sqrt(Math.max(80, s.T) / 298);
          var heat = Math.min(1, (s.T - 200) / 200);
          ctx.fillStyle = 'rgb(' + Math.round(80+175*heat) + ',' + Math.round(180-80*heat) + ',80)';
          for (var i=0;i<count;i++){
            var px = left + 12 + (chamberW-24) * (0.5 + 0.46*Math.sin(phase*speed*3.1+i*1.7));
            var py = top + 18 + (chamberH-30) * (0.5 + 0.46*Math.cos(phase*speed*2.4+i*1.3));
            ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI*2); ctx.fill();
          }
          ctx.fillStyle = t.ink;
          ctx.font = '12px sans-serif';
          ctx.fillText('活塞体积 ∝ V', left + chamberW + 16, 28);
          ctx.fillText('P=' + s.P.toFixed(0) + ' Pa', left + chamberW + 16, 48);
        } else if (s.kind === 'enzyme'){
          var pad = 26, left = pad, right = w - 12, top = 18, bottom = h - 22;
          var span = Math.max(40, right - left), rise = Math.max(24, bottom - top);
          var peak = 0.05, tSample, rSample, i;
          for (tSample = 0; tSample <= 80; tSample += 2){
            rSample = enzymeRate(tSample, s.Ea);
            if (rSample > peak) peak = rSample;
          }
          var rateY = function(rateVal){
            return bottom - rise * Math.min(1, Math.max(0, rateVal / peak));
          };
          var tempX = function(tempC){
            return left + span * Math.min(1, Math.max(0, tempC / 80));
          };
          ctx.strokeStyle = t.line; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(left, top); ctx.lineTo(left, bottom); ctx.lineTo(right, bottom); ctx.stroke();
          ctx.strokeStyle = t.accent; ctx.lineWidth = 2;
          ctx.beginPath();
          for (i = 0; i <= 80; i += 2){
            var px = tempX(i), py = rateY(enzymeRate(i, s.Ea));
            if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
          }
          ctx.stroke();
          var markX = tempX(s.T), markY = rateY(s.rate);
          ctx.fillStyle = t.warn;
          ctx.beginPath(); ctx.arc(markX, markY, 5, 0, Math.PI * 2); ctx.fill();
          var scanT = (phase * 22) % 80;
          var scanX = tempX(scanT), scanY = rateY(enzymeRate(scanT, s.Ea));
          ctx.fillStyle = t.accent;
          ctx.beginPath(); ctx.arc(scanX, scanY, 3 + Math.sin(phase * 6), 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = t.ink;
          ctx.font = '11px sans-serif';
          ctx.fillText('T/°C', right - 28, bottom - 4);
          ctx.fillText('rate', left + 4, top + 10);
          ctx.fillText('最适', Math.min(right - 28, Math.max(left + 4, tempX(44) - 10)), top + 10);
        } else if (s.kind === 'photo'){
          var light = Math.min(1, Math.max(0, s.I / 100));
          var co2 = Math.min(1, Math.max(0, (s.C - 100) / 700));
          function leaf(color, rx, ry){
            ctx.fillStyle = color;
            ctx.beginPath();
            if (typeof ctx.ellipse === 'function') ctx.ellipse(w*0.40, h*0.58, rx, ry, 0, 0, Math.PI*2);
            else ctx.arc(w*0.40, h*0.58, Math.min(rx, ry), 0, Math.PI*2);
            ctx.fill();
          }
          leaf('#14532d', w*0.16, h*0.26);
          leaf('#22c55e', w*0.12, h*0.20);
          ctx.strokeStyle = t.warn; ctx.lineWidth = 2;
          for (var ray=0; ray<5; ray++){
            ctx.globalAlpha = 0.25 + 0.7*light;
            ctx.beginPath();
            ctx.moveTo(14, 12 + ray*4);
            ctx.lineTo(w*0.28, h*0.34 + ray*6);
            ctx.stroke();
          }
          ctx.globalAlpha = 1;
          ctx.fillStyle = t.muted;
          var dots = Math.max(2, Math.round(2 + co2*6));
          for (var d=0; d<dots; d++){
            ctx.beginPath();
            ctx.arc(16 + (d%3)*9, h*0.68 + (d%4)*7, 3, 0, Math.PI*2);
            ctx.fill();
          }
          var bubbles = 3 + Math.round(s.rate * 8);
          ctx.fillStyle = '#7dd3fc';
          for (var b=0; b<bubbles; b++){
            var u = (phase * (0.45 + s.rate) + b * 0.16) % 1;
            var bx = w*0.70 + 12*Math.sin(phase*2.4 + b);
            var by = h*0.84 - u * (h*0.68);
            ctx.beginPath(); ctx.arc(bx, by, 3 + 4*s.rate, 0, Math.PI*2); ctx.fill();
          }
          ctx.fillStyle = t.accent;
          ctx.fillRect(w*0.08, h-14, Math.max(8, (w*0.84) * s.rate), 6);
          ctx.fillStyle = t.ink;
          ctx.font = '11px sans-serif';
          ctx.fillText('光照 I=' + s.I.toFixed(0) + '%', 10, 16);
          ctx.fillText('叶', w*0.37, h*0.58);
          ctx.fillText('O₂', w*0.72, 16);
          ctx.fillText('CO₂', 10, h-20);
          ctx.fillText('rate=' + s.rate.toFixed(2), w*0.55, h-20);
        } else {
          var x = 40 + (w-80) * (0.15 + 0.35*(1-Math.cos(phase * Math.min(2, s.a/4))));
          ctx.fillStyle = '#22c55e'; ctx.fillRect(x, h*0.45, 24, 24);
        }
      }
    };
  })();
"""


_TIME_INTEGRATOR_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var theta = 0.35, omega = 0, y = 20, v = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function period(L,g){ return 2*Math.PI*Math.sqrt(Math.max(0.05,L)/Math.max(0.1,g)); }
    return {
      applyParams: function(){
        if (recipe === 'pendulum'){
          var L = num('param-L',1), g = num('param-g',9.8);
          setReadout('T=' + period(L,g).toFixed(3) + ' s');
        } else {
          var g2 = num('param-g',9.8), tHint = Math.sqrt(2*Math.max(0.1,y)/g2);
          setReadout('v=' + v.toFixed(2) + ' m/s');
          return tHint;
        }
      },
      onStart: function(){
        if (recipe === 'pendulum' && Math.abs(theta) < 0.05) theta = num('param-theta0', 0.35);
        if (recipe === 'free_fall' && y < 0.2) y = num('param-y0', 20);
      },
      reset: function(){
        if (recipe === 'pendulum'){ theta = num('param-theta0', 0.35); omega = 0; }
        else { y = num('param-y0', 20); v = 0; }
        this.applyParams();
      },
      step: function(dt){
        if (recipe === 'pendulum'){
          var L = Math.max(0.2, num('param-L',1)), g = num('param-g',9.8);
          omega += -(g/L) * Math.sin(theta) * dt;
          theta += omega * dt;
          setReadout('T=' + period(L,g).toFixed(3) + ' s  θ=' + theta.toFixed(3));
        } else {
          var g = num('param-g',9.8);
          if (y > 0){ v += g*dt; y = Math.max(0, y - v*dt); }
          setReadout('v=' + v.toFixed(2) + ' m/s  y=' + y.toFixed(2) + ' m');
        }
      },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        ctx.clearRect(0,0,w,h);
        var t = workTokens();
        ctx.fillStyle = t.bg; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = t.ink; ctx.fillStyle = t.accent;
        if (recipe === 'pendulum'){
          var cx = w/2, cy = 16, Lpx = Math.min(h-36, 80 + num('param-L',1)*40);
          var x = cx + Lpx * Math.sin(theta), yb = cy + Lpx * Math.cos(theta);
          ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(x,yb); ctx.stroke();
          ctx.beginPath(); ctx.arc(x,yb,10,0,Math.PI*2); ctx.fill();
        } else {
          var y0 = Math.max(1, num('param-y0',20));
          var pad = 20;
          var groundY = h - pad;
          var topY = pad + 10;
          var span = Math.max(40, groundY - topY);
          var py = topY + span * (1 - y / y0);
          ctx.strokeStyle = t.line;
          ctx.beginPath(); ctx.moveTo(pad, topY); ctx.lineTo(pad, groundY); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(w * 0.22, groundY); ctx.lineTo(w * 0.82, groundY); ctx.stroke();
          ctx.fillStyle = t.ink;
          ctx.font = '12px sans-serif';
          ctx.fillText('y0', pad + 6, topY);
          ctx.fillText('地面', Math.max(pad + 6, w * 0.55), groundY - 6);
          ctx.fillStyle = t.accent;
          ctx.fillRect(w / 2 - 8, py, 16, 16);
          ctx.fillStyle = t.ink;
          ctx.fillText('m', w / 2 - 5, Math.max(topY, py - 6));
          var labelY = Math.min(groundY - 22, Math.max(topY + 14, py + 16));
          ctx.fillText('y=' + y.toFixed(1) + ' m', pad + 6, labelY);
          ctx.fillText('v=' + v.toFixed(1) + ' m/s', pad + 6, Math.min(groundY - 8, labelY + 14));
          if (v > 0.05){
            var tip = Math.min(groundY - 4, py + 16 + Math.min(36, v * 2));
            ctx.strokeStyle = t.warn;
            ctx.beginPath(); ctx.moveTo(w / 2, py + 16); ctx.lineTo(w / 2, tip); ctx.stroke();
          }
        }
      }
    };
  })();
"""


_WAVE_JS = r"""
  window.WorkFamily = (function(){
    var time = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function phaseOffset(){
      return num('param-phi', 1.2);
    }
    return {
      applyParams: function(){
        var A1 = num('param-A1',0.6), A2 = num('param-A2',0.6);
        var lam = num('param-lambda',80), phi = phaseOffset();
        setReadout('λ=' + lam.toFixed(0) + '  φ=' + phi.toFixed(2) + ' rad  A1+A2=' + (A1+A2).toFixed(2) + ' (axis uses 2A)');
      },
      onStart: function(){ if (time === 0) time = 0.2; },
      reset: function(){ time = 0.2; this.applyParams(); },
      step: function(dt){ time += dt * 2.2; this.applyParams(); },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        var A1 = num('param-A1',0.6), A2 = num('param-A2',0.6);
        var lam = Math.max(8, num('param-lambda',80));
        var phi = phaseOffset();
        var A = Math.max(0.2, A1+A2);
        var mid = h/2, amp = (h/2 - 20) / (2*Math.max(A, 0.4));
        var k = 2*Math.PI/lam;
        function sample(x){
          return A1*Math.sin(k*x - time) + A2*Math.sin(k*x - time + phi);
        }
        ctx.clearRect(0,0,w,h);
        var t = workTokens();
        ctx.fillStyle = t.bg; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = t.line;
        ctx.beginPath(); ctx.moveTo(0,mid); ctx.lineTo(w,mid); ctx.stroke();
        function strokeWave(color, width, fn){
          ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
          for (var x=0;x<w;x++){
            var py = mid - fn(x) * amp;
            if (x===0) ctx.moveTo(x,py); else ctx.lineTo(x,py);
          }
          ctx.stroke();
        }
        strokeWave(t.accent, 1.25, function(x){ return A1*Math.sin(k*x - time); });
        strokeWave(t.warn, 1.25, function(x){ return A2*Math.sin(k*x - time + phi); });
        strokeWave(t.ink, 2.2, sample);
        ctx.fillStyle = t.ink;
        ctx.font = '12px sans-serif';
        ctx.fillText('φ=' + phi.toFixed(2), 16, 18);
        ctx.fillText('λ=' + lam.toFixed(0), 16, 34);
      }
    };
  })();
"""


_RAY_OPTICS_JS = r"""
  window.WorkFamily = (function(){
    var angleDeg = 30;
    var angleRad = 30 * Math.PI / 180;
    var pulse = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function syncAngle(){
      angleDeg = Math.max(10, Math.min(70, num('param-theta', 30)));
      angleRad = angleDeg * Math.PI / 180;
      setReadout('θᵢ=' + angleDeg.toFixed(0) + '°  θᵣ=' + angleDeg.toFixed(0) + '°  反射定律 θᵢ=θᵣ');
      return angleRad;
    }
    function arrow(ctx, x1, y1, x2, y2){
      ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
      var ang = Math.atan2(y2-y1, x2-x1);
      ctx.beginPath();
      ctx.moveTo(x2,y2);
      ctx.lineTo(x2 - 10*Math.cos(ang-0.4), y2 - 10*Math.sin(ang-0.4));
      ctx.lineTo(x2 - 10*Math.cos(ang+0.4), y2 - 10*Math.sin(ang+0.4));
      ctx.closePath(); ctx.fill();
    }
    return {
      applyParams: function(){ syncAngle(); },
      onStart: function(){ if (pulse === 0) pulse = 0.05; },
      reset: function(){ pulse = 0; syncAngle(); },
      step: function(dt){ pulse = (pulse + dt * 0.55) % 2; },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        syncAngle();
        var w = canvas.width, h = canvas.height;
        var pad = 22;
        var cy = h / 2;
        var length = Math.min((w - 2 * pad) * 0.28, h / 2 - pad - 8);
        var mx = Math.min(w - pad - 20, Math.max(pad + 24 + length, w * 0.58));
        var ix = mx - length * Math.cos(angleRad);
        var iy = cy - length * Math.sin(angleRad);
        var rx = mx - length * Math.cos(angleRad);
        var ry = cy + length * Math.sin(angleRad);
        var nx = mx - Math.min(length * 0.72, mx - pad);
        ctx.clearRect(0,0,w,h);
        var t = workTokens();
        ctx.fillStyle = t.bg; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = t.ink; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(mx, pad); ctx.lineTo(mx, h-pad); ctx.stroke();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = t.line; ctx.setLineDash([5,4]);
        ctx.beginPath(); ctx.moveTo(mx, cy); ctx.lineTo(nx, cy); ctx.stroke();
        ctx.setLineDash([]);
        ctx.strokeStyle = t.accent; ctx.fillStyle = t.accent;
        arrow(ctx, ix, iy, mx, cy);
        ctx.strokeStyle = t.warn; ctx.fillStyle = t.warn;
        arrow(ctx, mx, cy, rx, ry);
        ctx.strokeStyle = t.ink;
        ctx.beginPath(); ctx.arc(mx, cy, Math.min(36, length*0.28), Math.PI-angleRad, Math.PI, false); ctx.stroke();
        ctx.beginPath(); ctx.arc(mx, cy, Math.min(36, length*0.28), Math.PI, Math.PI+angleRad, false); ctx.stroke();
        if (pulse > 0){
          var u = pulse <= 1 ? pulse : pulse - 1;
          var px = pulse <= 1 ? ix + (mx-ix)*u : mx + (rx-mx)*u;
          var py = pulse <= 1 ? iy + (cy-iy)*u : cy + (ry-cy)*u;
          ctx.fillStyle = t.accent;
          ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI*2); ctx.fill();
        }
        ctx.fillStyle = t.ink;
        ctx.font = '12px sans-serif';
        ctx.fillText('镜', Math.max(pad, mx - 20), h - pad);
        ctx.fillText('法线', Math.max(pad, (nx + mx) / 2 - 12), Math.max(pad + 12, cy - 12));
        ctx.fillText('入射角 '+angleDeg.toFixed(0)+'°', Math.max(pad, ix + 8), Math.max(pad + 12, iy + 14));
        ctx.fillText('反射角 '+angleDeg.toFixed(0)+'°', Math.max(pad, rx + 8), Math.min(h - pad, ry - 6));
      }
    };
  })();
"""


_COMPARTMENT_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var vin = 0.32, vout = 0.58, x = 1.2, y = 0.6;
    var hist = [];
    var maxHist = 160;
    var delta = 0.4;
    var flow = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function record(){
      hist.push([x, y]);
      if (hist.length > maxHist) hist.shift();
    }
    function osmosisFlux(){
      return num('param-k',0.15) * (num('param-cin',0.8) - num('param-cout',0.2));
    }
    function clampVol(v){
      return Math.min(0.85, Math.max(0.15, v));
    }
    function ensureOsmosisOffset(){
      if (Math.abs(vin - vout) < 0.12){ vin = 0.32; vout = 0.58; }
    }
    return {
      applyParams: function(){
        if (recipe === 'osmosis'){
          var J0 = osmosisFlux();
          setReadout('J=' + J0.toFixed(3) + '  Vin=' + vin.toFixed(2) + '  Vout=' + vout.toFixed(2));
        } else {
          setReadout('猎物x=' + x.toFixed(2) + '  捕食者y=' + y.toFixed(2) + '  δ=' + delta.toFixed(2));
        }
      },
      onStart: function(){
        if (recipe === 'osmosis'){ ensureOsmosisOffset(); if (flow < 0.05) flow = 0.05; }
        if (recipe === 'population'){
          if (x < 0.2){ x = 1.2; y = 0.6; }
          if (!hist.length) record();
        }
      },
      reset: function(){
        if (recipe === 'osmosis'){ vin = 0.32; vout = 0.58; flow = 0; }
        else { x = 1.2; y = 0.6; hist = []; record(); }
        this.applyParams();
      },
      step: function(dt){
        if (recipe === 'osmosis'){
          var cin = num('param-cin',0.8), cout = num('param-cout',0.2), k = num('param-k',0.15);
          var J = k * (cin - cout);
          vin = clampVol(vin + J*dt*1.2);
          vout = clampVol(vout - J*dt*1.2);
          flow += dt;
          setReadout('J=' + J.toFixed(3) + '  Vin=' + vin.toFixed(2) + '  Vout=' + vout.toFixed(2));
        } else {
          var a = num('param-alpha',0.8), b = num('param-beta',0.5), g = num('param-gamma',0.4);
          var dx = x * (a - b*y);
          var dy = y * (delta*x - g);
          x = Math.max(0.02, x + dx*dt);
          y = Math.max(0.02, y + dy*dt);
          record();
          setReadout('猎物x=' + x.toFixed(2) + '  捕食者y=' + y.toFixed(2));
        }
      },
      draw: function(ctx, canvas){
        if (!ctx || !canvas || typeof ctx.clearRect !== 'function') return;
        var w = canvas.width || 0, h = canvas.height || 0;
        if (w < 8 || h < 8) return;
        ctx.clearRect(0,0,w,h);
        var t = workTokens();
        ctx.fillStyle = t.bg; ctx.fillRect(0,0,w,h);
        if (recipe === 'osmosis'){
          var cin = num('param-cin',0.8), cout = num('param-cout',0.2);
          var J = osmosisFlux();
          var left = 16, boxW = w * 0.32, mid = w * 0.47, right = mid + 14;
          ctx.fillStyle = '#0ea5e9';
          ctx.fillRect(left, h*(1-vin), boxW, h*vin);
          ctx.fillStyle = '#14b8a6';
          ctx.fillRect(right, h*(1-vout), boxW, h*vout);
          if (ctx.setLineDash) ctx.setLineDash([3,3]);
          ctx.strokeStyle = t.ink;
          ctx.lineWidth = 2;
          ctx.strokeRect(mid, 12, 10, h-24);
          if (ctx.setLineDash) ctx.setLineDash([]);
          ctx.fillStyle = t.ink;
          ctx.font = '11px sans-serif';
          ctx.fillText('内侧', left + 6, 16);
          ctx.fillText('c=' + cin.toFixed(2), left + 6, 30);
          ctx.fillText('外侧', right + 6, 16);
          ctx.fillText('c=' + cout.toFixed(2), right + 6, 30);
          ctx.fillText('半透膜', Math.max(left, mid - 18), h - 8);
          var dir = J >= 0 ? 1 : -1;
          var i, u, px, py;
          ctx.fillStyle = t.accent;
          for (i=0;i<5;i++){
            u = (flow * 0.7 + i * 0.18) % 1;
            if (dir < 0) u = 1 - u;
            px = left + boxW + (right - left - boxW) * u;
            py = h * 0.45 + 8 * Math.sin(flow * 4 + i);
            ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI*2); ctx.fill();
          }
        } else {
          var pad = 22, mid = Math.floor(w * 0.58);
          ctx.font = '12px sans-serif';
          ctx.fillStyle = '#22c55e'; ctx.fillRect(pad, 6, 8, 8);
          ctx.fillStyle = t.ink; ctx.fillText('猎物 x', pad + 12, 14);
          ctx.fillStyle = '#f97316'; ctx.fillRect(pad + 80, 6, 8, 8);
          ctx.fillStyle = t.ink; ctx.fillText('捕食者 y', pad + 92, 14);
          ctx.strokeStyle = t.line;
          ctx.beginPath(); ctx.moveTo(pad, h-pad); ctx.lineTo(mid-10, h-pad); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(mid, h-pad); ctx.lineTo(w-12, h-pad); ctx.stroke();
          if (hist.length){
            var maxX = 0.2, maxY = 0.2, i, px, py;
            for (i=0;i<hist.length;i++){ if (hist[i][0]>maxX) maxX=hist[i][0]; if (hist[i][1]>maxY) maxY=hist[i][1]; }
            maxX *= 1.2; maxY *= 1.2;
            ctx.strokeStyle = '#22c55e'; ctx.beginPath();
            for (i=0;i<hist.length;i++){
              px = pad + (mid-10-pad) * (i / Math.max(1, maxHist-1));
              py = h-pad - (h-2*pad) * (hist[i][0]/maxX);
              if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
            }
            ctx.stroke();
            ctx.strokeStyle = '#f97316'; ctx.beginPath();
            for (i=0;i<hist.length;i++){
              px = pad + (mid-10-pad) * (i / Math.max(1, maxHist-1));
              py = h-pad - (h-2*pad) * (hist[i][1]/maxY);
              if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
            }
            ctx.stroke();
            ctx.fillStyle = t.accent;
            for (i=0;i<hist.length;i++){
              px = mid + (w-12-mid) * (hist[i][0]/maxX);
              py = h-pad - (h-2*pad) * (hist[i][1]/maxY);
              ctx.fillRect(px, py, 2, 2);
            }
          }
        }
      }
    };
  })();
"""


_CONVERTER_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var M_TO_FT = 3.280839895;
    var initialValue = recipe === "temp_c_f" ? "0" : "1";
    var initialFrom = recipe === "temp_c_f" ? "C" : "m";
    var initialTo = recipe === "temp_c_f" ? "F" : "ft";
    function convert(value, from, to){
      if (!isFinite(value)) return null;
      if (from === to) return value;
      if (recipe === "temp_c_f"){
        if (from === "C" && to === "F") return value * 9 / 5 + 32;
        if (from === "F" && to === "C") return (value - 32) * 5 / 9;
        return null;
      }
      if (from === "m" && to === "ft") return value * M_TO_FT;
      if (from === "ft" && to === "m") return value / M_TO_FT;
      return null;
    }
    function format(value, from, to){
      var n = Number(value);
      if (!isFinite(n)) return "无效输入";
      var rounded = Math.abs(n) >= 1000 ? n.toFixed(2) : n.toFixed(4);
      return rounded.replace(/\.?0+$/, "") + " " + to + "  ← " + from;
    }
    return {
      initialValue: initialValue,
      initialFrom: initialFrom,
      initialTo: initialTo,
      convert: convert,
      format: format,
      reset: function(){ return {value: initialValue, from: initialFrom, to: initialTo}; }
    };
  })();
"""
