def get_logic_extraction_prompt(field, diagram_type, user_content, Emphasized_content):
    """
    对应 PRD P18 的 Prompt 1: 框架梳理 [cite: 494, 495]
    """
    # 使用 f-string，用 {变量名} 作为占位符
    return f"""
#role：你现在的任务是“科研逻辑梳理员”。我将提供我论文的逻辑大纲。请你严格按照后面的【输出限制】进行回复，不要开始画图，不要讨论视觉风格，只负责梳理逻辑结构。

#论文逻辑如下: 
##A. 核心身份 
###领域: {field} 
###图表类型: {diagram_type} 

##B. 流程步骤 (Technical Breakdown)
{user_content}

##C. 关键变量与关系
###需要强调的交互：
{Emphasized_content}

#输出限制 (Constraint)
请仅输出一个结构化的 Markdown 表格或层级列表，格式如下。禁止输出任何总结性废话或客套话。
输出格式示例：
1. 逻辑流验证 (Logical Flow)
[Step 1] 输入: ... -> 技术: ... -> 输出: ...
[Step 2] 输入: ... -> 技术: ... -> 输出: ...
2. 关键实体提取 (Key Entities)
- (列出所有需要可视化的具体组件，如：Bi-GRU 模块、求和操作符、残差连线)
3. 潜在逻辑缺口 (Gap Analysis)
(如果逻辑完整，请填“无”；如果有断层，请指出，例如：“Step 2 的输出 $$h_t$$ 在 Step 3 中未被使用，是否正确？”)
"""


def get_framework_with_Sketch(Logical_Arc, sketch):
    return f"""
#Role：你现在的任务是“科研绘图视觉解码师”。
我将提供两份输入：
1. 【逻辑架构】：这是我们之前确认过的论文逻辑结构。
2. 【手绘草图】：这是我画的布局草稿（已上传图片）。
请结合这两者，分析出我想要的视觉元素细节。你需要把草图中的几何图形映射回逻辑组件。
##Input 1:逻辑架构
{Logical_Arc}
##Input 2:手绘草图
{sketch}
图片描述补充：
#Task任务要求
请仔细观察草图，并回答以下问题，输出为结构化报告：
1. 全局布局 (Global Layout)：流向是“从左到右”还是“从上到下”？是否分为了几个大的区域（用虚线框还是背景色块区分）？
2. 组件映射 (Component Mapping)：草图中的图形分别代表了逻辑里的哪个技术点？（例如：那个画了三条线的方块对应的是 Transformer 吗？）
3. 连接与交互 (Connections)：箭头是实线还是虚线？有没有特殊的合并符号（如 $\oplus$ 或 $\otimes$）？
#输出限制：
请仅输出以下 Markdown 表格和列表，不要废话：
1. 布局拓扑 (Topology)
- 整体流向: [例如：水平流向，左入右出]
- 分区: [例如：草图显示分为 "Encoder" (左) 和 "Decoder" (右) 两个大虚线框]
2. 元素视觉映射表 (Element Mapping Table)
3. 连线与符号 (Connectors)
- 主数据流: [例如：粗实线箭头]
- 辅助/残差流: [例如：弯曲的虚线箭头，跨越了 Step 2]
- 数学算符: [例如：草图中画了一个圈加个点，代表 Element-wise Product]
4. 待确认细节 (Ambiguities)
(如果你看不清草图的某个部分，或者草图与逻辑有冲突，请在此列出疑问。例如：“草图右上角的那个三角形在逻辑总结中没有对应，它是 loss function 吗？”)
"""


def get_framework_without_Sketch(Logical_Arc):
    return f"""
#Role：你现在的任务是“科研绘图视觉解码师”。
我将提供一份输入：
【逻辑架构】：这是我们之前确认过的论文逻辑结构。
请结合该领域顶级会议或期刊的风格分析出我想要的视觉元素细节。你需要把草图中的几何图形映射回逻辑组件。
##Input 1:逻辑架构
{Logical_Arc}
#Task任务要求
请仔细观察草图，并回答以下问题，输出为结构化报告：
1. 全局布局 (Global Layout)：流向是“从左到右”还是“从上到下”？是否分为了几个大的区域（用虚线框还是背景色块区分）？
2. 组件映射 (Component Mapping)：草图中的图形分别代表了逻辑里的哪个技术点？（例如：那个画了三条线的方块对应的是 Transformer 吗？）
3. 连接与交互 (Connections)：箭头是实线还是虚线？有没有特殊的合并符号（如 $\oplus$ 或 $\otimes$）？
#输出限制：
请仅输出以下 Markdown 表格和列表，不要废话：
4. 布局拓扑 (Topology)
- 整体流向: [例如：水平流向，左入右出]
- 分区: [例如：草图显示分为 "Encoder" (左) 和 "Decoder" (右) 两个大虚线框]
5. 元素视觉映射表 (Element Mapping Table)
6. 连线与符号 (Connectors)
- 主数据流: [例如：粗实线箭头]
- 辅助/残差流: [例如：弯曲的虚线箭头，跨越了 Step 2]
- 数学算符: [例如：草图中画了一个圈加个点，代表 Element-wise Product]
7. 待确认细节 (Ambiguities)
(如果你看不清草图的某个部分，或者草图与逻辑有冲突，请在此列出疑问。例如：“草图右上角的那个三角形在逻辑总结中没有对应，它是 loss function 吗？”)
"""


def get_Visual_Element_Supplement(Logical_Arc, Supplement):
    return f"""
#角色：
你现在的任务是“科研绘图视觉定稿师”。我们将进入最终 Prompt 生成前的最后一步：视觉规格定义。
请根据我提供的【逻辑架构】和【补充细节】，生成一份详尽的、无歧义的“视觉元素规格书”。
输入 1：逻辑架构 (来自 Phase 1)
{Logical_Arc}
输入 2：用户补充细节 (User Supplements) 
{Supplement}
#任务要求
将上述逻辑和细节合并，消除任何模糊地带。如果不确定某个元素的颜色或形状，请根据“高顶刊 (Top-tier Conference)”风格自动补全。
#输出限制：
请仅输出以下 Markdown 格式的【视觉元素规格书 (Visual Specification Sheet)】。这是生成最终绘图指令的依据，必须极度具体。
输出格式示例 (严格遵守)：
1. 全局风格定义 (Global Style)
- 配色方案: [例如：Muted Teal (#008080) & Coral Orange (#FF7F50), White Background]
- 维度: [例如：2.5D Isometric (等轴测图) 或 Flat Vector (扁平矢量)]
- 线条: [例如：Thin, dark grey stroke, rounded corners]
2. 节点/组件规格 (Node Specs)
- [输入层]: 形状=[...], 颜色=[...], 标签=[LaTeX格式]
- [核心处理层]: 形状=[...], 颜色=[...], 标签=[LaTeX格式], 特殊效果=[例如：Drop shadow]
- [输出层]: 形状=[...], 颜色=[...], 标签=[LaTeX格式]
3. 连接与流向 (Edges & Flow)
- 主要数据流: 样式=[实线/虚线], 颜色=[...], 箭头类型=[...]
- 特殊交互: [例如：Step 2 -> Step 1 的反馈线，使用红色弯曲虚线]
4. 布局微调 (Layout Refinement)
- [例如：为了强调 Step 2，将其放大 1.2 倍并置于画面正中央]
- [例如：输入和输出保持在同一水平线上]
"""


