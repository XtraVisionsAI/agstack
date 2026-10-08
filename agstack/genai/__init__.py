#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.genai——生成式 AI 三层（3.0）

- :mod:`.llm`：模型接入（client / hooks / prompts / token），不依赖另外两层；
- :mod:`.flow`：编排（Agent / Tool / Flow / nodes / guards），依赖 llm；
- :mod:`.harness`：运行时纪律（ports / events / projection / metering / context / overflow / spill），依赖 llm 与 flow。

依赖单向、不成环；应用对本包的依赖应集中在一处适配层。
"""
