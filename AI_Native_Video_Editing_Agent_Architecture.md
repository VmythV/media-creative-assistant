# AI Native Video Editing Agent 架构设计总结

## 1. 核心理念

AI 不应该替代剪辑师，而应该作为剪辑师的 Copilot：

-   人负责创意、审美、最终决策。
-   AI 负责理解素材、提供剪辑方向、执行重复工作。

目标：

> Human Creativity + AI Intelligence + Professional Editing Tools

------------------------------------------------------------------------

# 2. 总体架构

    用户
     |
    交互层(Web/Desktop/IM)
     |
    Conversation Layer
     |
    Agent Runtime
     |
    --------------------------------
    |              |               |
    Planning    Understanding    Memory
    Agent       Agent            System
     |
    Video Intelligence Layer
     |
    --------------------------------
    |              |
    Vision Model   Audio Model
     |
    Editing IR（视频编辑中间表示）
     |
    --------------------------------
    |              |              |
    DaVinci    Premiere        FFmpeg
    Adapter    Adapter
     |
    最终视频

------------------------------------------------------------------------

# 3. Editing IR（视频编辑中间语言）

不要让 AI 直接控制具体软件。

错误：

    AI -> DaVinci API

正确：

    AI
     |
    Editing Plan
     |
    Editing IR
     |
    DaVinci Adapter
    Premiere Adapter

例如：

``` json
{
  "timeline": [
    {
      "type": "clip",
      "source": "video001.mp4",
      "trim": {
        "start": 3,
        "end": 12
      },
      "role": "opening"
    },
    {
      "type": "subtitle",
      "content": "Tokyo Night"
    }
  ]
}
```

这样未来可以支持：

-   DaVinci Resolve
-   Adobe Premiere
-   After Effects
-   CapCut

------------------------------------------------------------------------

# 4. 剪辑软件适配层

## DaVinci Resolve

优势：

-   Python/Lua Scripting API
-   Timeline 控制
-   Fusion 节点系统
-   工程导入导出

适合做第一版 AI 剪辑 Agent。

能力：

-   创建项目
-   导入素材
-   创建 Timeline
-   添加字幕
-   控制 Fusion 特效
-   自动渲染

------------------------------------------------------------------------

## Premiere Pro

支持：

-   ExtendScript
-   CEP/UXP 插件

优势：

-   商业生态强
-   行业使用广泛

------------------------------------------------------------------------

## After Effects

更适合：

-   AI 特效生成
-   动画
-   Motion Graphics

------------------------------------------------------------------------

# 5. MCP Tool Layer

AI 不直接调用软件。

通过 MCP：

    Agent

     |

    MCP Runtime

     |

    ------------------

    Resolve MCP

    FFmpeg MCP

    Storage MCP

    Vision MCP

例如：

工具：

    create_timeline()

    add_subtitle()

    render_video()

    extract_audio()

实际由：

-   Resolve API
-   FFmpeg
-   其他工具

执行。

------------------------------------------------------------------------

# 6. Capability Discovery（能力发现）

类似 open-design 自动检测 Codex CLI / Claude Code。

启动时扫描环境：

    AI Video Runtime

    启动

    ↓

    Capability Discovery

    ↓

    发现：

    模型：
    - GPT
    - Claude
    - Qwen

    视觉：
    - 云端 Vision
    - 本地模型

    剪辑：
    - DaVinci
    - Premiere

    工具：
    - FFmpeg

    交互：
    - Web
    - IM

------------------------------------------------------------------------

## Capability Registry

示例：

``` json
{
 "capabilities": [
   {
     "type":"editor",
     "name":"davinci",
     "features":[
       "timeline",
       "subtitle",
       "fusion"
     ]
   }
 ]
}
```

Agent 根据能力动态选择方案。

------------------------------------------------------------------------

# 7. Model Provider 抽象

视觉模型：

    Vision Interface

            |

    ------------------

    OpenAI Vision

    Claude Vision

    Qwen-VL

    LLaVA

LLM：

    LLM Interface

            |

    ------------------

    GPT

    Claude

    Local LLM

    Ollama

业务逻辑不绑定模型。

------------------------------------------------------------------------

# 8. User Memory 设计

Memory 必须接口化。

不能只有一个用户记忆。

应该：

    Memory

    |
    |-- Global Memory
    |
    |-- User Memory
    |
    |-- Business Profile
    |
    |-- Project Memory
    |
    |-- Temporary Memory

------------------------------------------------------------------------

## 示例

旅行视频：

    Travel Profile

    {
     pace:"slow",
     color:"cinematic",
     music:"ambient"
    }

商业宣传：

    Business Profile

    {
     pace:"fast",
     subtitle:"strong",
     color:"brand"
    }

可以随时切换。

------------------------------------------------------------------------

# 9. Memory Provider

接口：

    MemoryProvider

    get()

    save()

    search()

实现：

-   SQLite
-   PostgreSQL
-   Vector Database
-   Cloud Memory

------------------------------------------------------------------------

# 10. Coding Agent 集成

Claude Code、Codex CLI 不应该成为整个系统。

它们应该是 Specialist Agent。

例如：

主 Agent：

    视频剪辑任务

发现：

需要开发 Resolve 插件。

调用：

    Claude Code Agent

    任务：

    开发 Fusion 插件

------------------------------------------------------------------------

# 11. Coding Agent 上下文问题

简单：

    claude -p

属于无状态调用。

问题：

-   上下文丢失
-   需要重复输入

更好的方式：

## Session

保存：

-   对话历史
-   工作目录
-   当前任务

或者：

## Agent Runtime

统一管理：

-   Context
-   Memory
-   Tools
-   Workspace

------------------------------------------------------------------------

# 12. 推荐最终系统架构

                        User

                         |

                  Interaction Layer

                         |

                   Agent Runtime

                         |

              Capability Discovery

                         |

    ------------------------------------------------

    Memory        Planning        Model Providers


                         |

                  Editing IR


                         |

                  MCP Runtime


    ------------------------------------------------

    DaVinci     Premiere     FFmpeg     Storage


                         |

                  Final Video

------------------------------------------------------------------------

# 13. 产品定位

不是：

"AI 自动剪视频"

而是：

"AI Native Creative Assistant"

类似：

Cursor 对程序员。

Figma AI 对设计师。

你的目标：

成为视频创作者的 AI 副驾驶。

------------------------------------------------------------------------

# 14. MVP 建议

第一阶段：

-   视频素材分析
-   自动分类
-   精彩片段推荐
-   剪辑方案生成
-   Resolve 工程生成

第二阶段：

-   AI 修改 Timeline
-   自动字幕
-   BGM 推荐
-   特效模板

第三阶段：

-   学习个人剪辑风格
-   多平台适配
-   企业级创作流程

------------------------------------------------------------------------

# 总结

整个系统最核心的不是某一个 API，而是：

1.  Editing IR：视频编辑标准中间层
2.  MCP Tool Layer：统一工具调用
3.  Capability Discovery：动态发现环境能力
4.  Memory System：学习人的创作习惯
5.  Agent Runtime：管理上下文和任务

最终形成：

AI + 人类创意 + 专业工具 的视频创作操作系统。
