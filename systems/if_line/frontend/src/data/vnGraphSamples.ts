import type { VNGraph } from './threeKingdoms'

// 本文件由 backend/vn_graph_*_output.json 拷贝而来，供 VNGraphPlayerView demo 页本地加载。
// 注意：这些是【编译前】产物，图片路径是 Godot res:// 形式；前端无真实图片时由播放器占位显示。

export interface VNGraphSample {
  key: string
  label: string
  description: string
  graph: VNGraph
}

const llmGraph: VNGraph = {
  "Version": 1,
  "StartNodeIndex": 1,
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "Comment": "",
      "NodeType": 1,
      "SubType": 6,
      "X": 80,
      "Y": 80,
      "Data": {},
      "Outputs": {
        "Next": [
          5
        ]
      }
    },
    {
      "Index": 2,
      "DisplayName": "背景: 燕宫",
      "Comment": "",
      "NodeType": 2,
      "SubType": 3,
      "X": 430,
      "Y": 430,
      "Data": {
        "BackgroundImage": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Backgrounds/chapter_1_燕宫.png"
        },
        "ChangeType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.5
        }
      },
      "Outputs": {}
    },
    {
      "Index": 3,
      "DisplayName": "太子丹立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 630,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "太子丹"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/太子丹.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 620,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 4,
      "DisplayName": "荆轲立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 1030,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "荆轲"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/荆轲.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 520,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 5,
      "DisplayName": "段落",
      "Comment": "",
      "NodeType": 1,
      "SubType": 2,
      "X": 430,
      "Y": 80,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "夜色压在燕宫的铜灯上。太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "太子丹"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆卿，秦兵不会等我们。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲沉默片刻，缓缓开口。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "荆轲"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "臣明白。但此行凶险，需万全准备。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "太子丹停下脚步，目光灼灼地看向荆轲。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "太子丹"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "我已等了太久。燕国的存亡，全系于你此行。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲抬起头，眼中闪过一丝决绝。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "荆轲"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "臣，愿往。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            }
          ]
        }
      },
      "Outputs": {
        "Next": [
          9
        ],
        "Actions": [
          2,
          3,
          4
        ]
      }
    },
    {
      "Index": 6,
      "DisplayName": "背景: 易水河畔",
      "Comment": "",
      "NodeType": 2,
      "SubType": 3,
      "X": 780,
      "Y": 430,
      "Data": {
        "BackgroundImage": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Backgrounds/chapter_1_易水河畔.png"
        },
        "ChangeType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.5
        }
      },
      "Outputs": {}
    },
    {
      "Index": 7,
      "DisplayName": "荆轲立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 980,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "荆轲"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/荆轲.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 520,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 8,
      "DisplayName": "高渐离立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 1380,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "高渐离"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/高渐离.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 720,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 9,
      "DisplayName": "段落",
      "Comment": "",
      "NodeType": 1,
      "SubType": 2,
      "X": 780,
      "Y": 80,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "数日后，易水河畔。秋风萧瑟，枯叶纷飞。高渐离击筑而歌。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "高渐离"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "风萧萧兮易水寒，壮士一去兮不复还！"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲转身，向燕宫方向深深一拜，随后头也不回地踏上征程。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            }
          ]
        }
      },
      "Outputs": {
        "Next": [],
        "Actions": [
          6,
          7,
          8
        ]
      }
    }
  ]
} as VNGraph

const ruleGraph: VNGraph = {
  "Version": 1,
  "StartNodeIndex": 1,
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "Comment": "",
      "NodeType": 1,
      "SubType": 6,
      "X": 80,
      "Y": 80,
      "Data": {},
      "Outputs": {
        "Next": [
          6
        ]
      }
    },
    {
      "Index": 2,
      "DisplayName": "背景切换",
      "Comment": "",
      "NodeType": 2,
      "SubType": 3,
      "X": 430,
      "Y": 430,
      "Data": {
        "BackgroundImage": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Backgrounds/chapter_1_scene.png"
        },
        "ChangeType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.5
        }
      },
      "Outputs": {}
    },
    {
      "Index": 3,
      "DisplayName": "背景音乐",
      "Comment": "",
      "NodeType": 2,
      "SubType": 21,
      "X": 630,
      "Y": 430,
      "Data": {
        "AudioPath": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/BGM/chapter_1_bgm.mp3"
        },
        "Volume": {
          "Kind": "Float",
          "NumberValue": 0.8
        },
        "FadeTime": {
          "Kind": "Float",
          "NumberValue": 2.0
        },
        "Loop": {
          "Kind": "Bool",
          "BoolValue": true
        }
      },
      "Outputs": {}
    },
    {
      "Index": 4,
      "DisplayName": "荆轲立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 830,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "荆轲"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/荆轲.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 520,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 5,
      "DisplayName": "太子丹立绘",
      "Comment": "",
      "NodeType": 2,
      "SubType": 1,
      "X": 1030,
      "Y": 430,
      "Data": {
        "TachiID": {
          "Kind": "String",
          "StringValue": "太子丹"
        },
        "TachiIamge": {
          "Kind": "String",
          "StringValue": "res://Resources/Generated/Tachi/太子丹.png"
        },
        "TargetPosition": {
          "Kind": "Vector2",
          "X": 720,
          "Y": 120
        },
        "EnterType": {
          "Kind": "Enum",
          "StringValue": "FadeIn"
        },
        "Duration": {
          "Kind": "Float",
          "NumberValue": 0.3
        }
      },
      "Outputs": {}
    },
    {
      "Index": 6,
      "DisplayName": "段落",
      "Comment": "",
      "NodeType": 1,
      "SubType": 2,
      "X": 430,
      "Y": 80,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "夜色压在燕宫的铜灯上。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "太子丹"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆卿，秦兵不会等我们。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲沉默片刻，缓缓开口。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "荆轲"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "臣明白。但此行凶险，需万全准备。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "太子丹停下脚步，目光灼灼地看向荆轲。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "太子丹"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "我已等了太久。燕国的存亡，全系于你此行。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲抬起头，眼中闪过一丝决绝。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "荆轲"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "臣，愿往。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "数日后，易水河畔。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "秋风萧瑟，枯叶纷飞。高渐离击筑而歌。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "高渐离"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "风萧萧兮易水寒，壮士一去兮不复还！"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {
                  "Kind": "String",
                  "StringValue": "旁白"
                },
                "Text": {
                  "Kind": "String",
                  "StringValue": "荆轲转身，向燕宫方向深深一拜，随后头也不回地踏上征程。"
                },
                "VoiceId": {
                  "Kind": "String",
                  "StringValue": ""
                }
              }
            }
          ]
        }
      },
      "Outputs": {
        "Next": [],
        "Actions": [
          2,
          3,
          4,
          5
        ]
      }
    }
  ]
} as VNGraph

export const llmSample: VNGraphSample = {
  key: 'llm',
  label: "荆轲刺秦（LLM 版）",
  description: "荆轲刺秦（LLM 生成的图，含背景/立绘/段落，2 段剧情）",
  graph: llmGraph,
}

export const ruleSample: VNGraphSample = {
  key: 'rule',
  label: "荆轲刺秦（规则版）",
  description: "荆轲刺秦（规则生成的图，含 BGM/背景/立绘/段落）",
  graph: ruleGraph,
}

const choiceGraph: VNGraph = {
  "Version": 1,
  "StartNodeIndex": 1,
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "Comment": "",
      "NodeType": 1,
      "SubType": 6,
      "X": 0,
      "Y": 0,
      "Data": {},
      "Outputs": {
        "Next": [
          2
        ]
      }
    },
    {
      "Index": 2,
      "DisplayName": "岔路口",
      "Comment": "",
      "NodeType": 1,
      "SubType": 5,
      "X": 0,
      "Y": 0,
      "Data": {
        "Options": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "Text": {
                  "Kind": "String",
                  "StringValue": "上山采药"
                }
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "Text": {
                  "Kind": "String",
                  "StringValue": "下水摸鱼"
                }
              }
            }
          ]
        }
      },
      "Outputs": {
        "Options[0].Next": [
          3
        ],
        "Options[1].Next": [
          4
        ]
      }
    },
    {
      "Index": 3,
      "DisplayName": "山路",
      "Comment": "",
      "NodeType": 1,
      "SubType": 1,
      "X": 0,
      "Y": 0,
      "Data": {
        "SpeakerIdData": {
          "Kind": "String",
          "StringValue": "旁白"
        },
        "TextData": {
          "Kind": "String",
          "StringValue": "山路崎岖，你采到一株灵芝。"
        },
        "VoiceIdData": {
          "Kind": "String",
          "StringValue": ""
        }
      },
      "Outputs": {
        "Next": [
          5
        ]
      }
    },
    {
      "Index": 4,
      "DisplayName": "水路",
      "Comment": "",
      "NodeType": 1,
      "SubType": 1,
      "X": 0,
      "Y": 0,
      "Data": {
        "SpeakerIdData": {
          "Kind": "String",
          "StringValue": "旁白"
        },
        "TextData": {
          "Kind": "String",
          "StringValue": "河水冰凉，你摸到一条锦鲤。"
        },
        "VoiceIdData": {
          "Kind": "String",
          "StringValue": ""
        }
      },
      "Outputs": {
        "Next": [
          5
        ]
      }
    },
    {
      "Index": 5,
      "DisplayName": "回家",
      "Comment": "",
      "NodeType": 1,
      "SubType": 1,
      "X": 0,
      "Y": 0,
      "Data": {
        "SpeakerIdData": {
          "Kind": "String",
          "StringValue": "旁白"
        },
        "TextData": {
          "Kind": "String",
          "StringValue": "日落西山，你满载而归。"
        },
        "VoiceIdData": {
          "Kind": "String",
          "StringValue": ""
        }
      },
      "Outputs": {
        "Next": []
      }
    }
  ]
} as VNGraph

export const choiceSample: VNGraphSample = {
  key: 'choice',
  label: '岔路口（含 Choice 分支）',
  description: '演示选择节点：两个选项分别走山路/水路，最终合流',
  graph: choiceGraph,
}

export const vnGraphSamples: VNGraphSample[] = [
  llmSample,
  ruleSample,
  choiceSample,
]
