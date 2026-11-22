"""The agent library, persisted in `agents` and edited from the Agents page. Workflows use agents as stages.

Agents are shared by every project (project SHARED). A project can customise one: it gets its own copy (same key,
project = the project's key), which that project uses instead of the shared one until it is reverted.
"""

import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import load_only

from app.agents.defaults import AUTO, CLAUDE_MODELS, DOCS, default_config
from app import cache, projects
from app.db.models import GLOBAL, SHARED, Agent, Run, Setting, WorkflowDef
from app.db.session import get_engine, session_scope
from app.orchestrator.stages import BUILTIN_STAGES, SUPERSEDED

log = logging.getLogger(__name__)

FIELDS = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs", "max_turns",
          "checks", "guardrails")
# Built-in agents added after the first release, keyed by the settings marker that records they were added once.
LATER_BUILTINS = {"_added_integration_design": ["integration_design"],
                  "_added_linked_domain_contracts": ["linked_domain_contracts"],
                  "_added_discovery_agents": ["bounded_contexts", "domain_features"],
                  "_added_modulith_design_agents": ["domain_dependencies", "domain_design",
                                                    "wrapper_design", "orchestrator_design"],
                  "_added_monolith_orchestrator": ["monolith_orchestrator"],
                  "_added_orchestrator_service": ["orchestrator_service"],
                  "_added_service_tests": ["service_tests"],
                  "_added_lean_agents": ["analysis", "modulith_design", "modernise_and_test"],
                  "_added_dependency_analysis": ["dependency_analysis"],
                  "_added_orchestrator_tests": ["orchestrator_tests"]}
# A built-in added later that takes over another's work starts with that agent's choices (stub or AI, model, effort),
# in the shared library and in each project's copy: else it would run as the stub where the other runs the AI.
LATER_SETTINGS_FROM = {"dependency_analysis": "analysis", "orchestrator_tests": "independent_tests"}

# SHA-256 of default instructions from earlier releases. A built-in agent still holding one of these was never edited,
# so it is safe to bring its instructions, required documents, tools and checks up to the current defaults.
PREVIOUS_DEFAULT_HASHES: dict[str, set[str]] = {
    # The lean workflow's analysis, and the feature listing: missing here before, so they never followed a release
    "analysis": {"3d4da07082efce79eadd4722ac6b87d06f1a693f065d91ca25a4aba244bbcf3d",
        "61ee61d1db291676aa9b418f002bd333c12ce77af995f975529a3cc5f411618c",
        "46f1c3485d5d38e5e5d88936028c3c040f34d3db2db8d1cabe158286713aae95",
                 "d2a9bf9051ad9e47669594a1ca6d46cc857c088b676d872fe3659f5a7581316e"},
    "domain_features": {"ce6ec2b4e1024f516af9bd2cc7be776b3add71b7441049e21609c328f5655a8f",
        "8d67a2c959d7590b0cdb369d65fe4265fc4a0291a8cc85fc1c00446eeb524e1f"},
    "boundary_design": {"0afe5047252464458ead3aac6b867502a91e1e62835dd59b7b7dcec8fbc8200d",
                        "f0df743aa52f57bfccb67748684cd0004993308476d3c5e538d94a3d9ba0ceea",
        "54005c5822e8c7380780ed9bd94127f80e83e35d8733616754436a17334070b5"},
    "deep_dive": {"a0bf087fe4ccf4df5533d0d01a762c5dfea5e866fd934d0083607cad2c15544e",
                  "9a50019f34790f8b4de82b88a85d7d0b5c7d877100d052ad2886fd66252dc4a3",
                  "e7932db2bbf7b61072ab89b6480fc7d34a7f64ce91db9cb502d3ceb5bbecfdd1"},
    "domain_dependencies": {"ce57e012db11db34e57dc3971aadde957595ea19a147970031ba617015403861",
        "d2e6c80aa47483757e655c6c803808e5ebfa2e90872ff8684efe257618cd3e33",
                            "e414b29584fc2df4dfd5d5a4bf1b67f2202e015b1bdbc9e992f937e41a7d51cd",
                              "d2fc147964a2e477c07072900e92b7356898e7613f3274d96dfab97576ec8476",
                            "6909abd6a1c273ef64d44d286532f0171c7c92c3d0cd78939ce8db8610c33d25",
                            "3d453659a94a2f241277ea0f3c3feb349d125fdd97057bb5bc1f1d39bd3dbcab"},
    "domain_design": {"c26565ba9acfcbe35b352c3cf6565b8ff6e245b01a280bacf94cc7cc06f770c9",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "9caf82f7ff22962b079c8b1fefdffc17a476f6d246f6d70ffb798ef3feb081ca",
        "dd198f239be4b92d142930b6e651ac90dadd1dd31d7209a8b43d70ca384b162d",
                      "9c129233aca1bf0a00dd893f4f0c44ab545f0ac5346a75b7222432e398bc2e31",
        "183bbce289171e4d9b20d7d1506417c2aaf10d85c76f278ae91f80e1cadb6405",
                              "45ad59b8a91325d3caa86f53505051bda11071e52282340470b7eef5fa0649eb",
                      "142d8a42236f9e844bcd3445647e9f34787655dece2bc6ccc0b05ec30862e3c8",
                      "5a7035410502576364098df1291b71788fdbe7e10be7644db0a102550a7f8ab7",
                      "e5ac314731ee2cea532d10917f03304365e33b91c1f7358103bbe9d314ddd8db",
        "12b7b3cd2ffc888c21f10cd8fab6141eb9140dd450081f452843ddd0662b1e84",
        "a51508117bdc207fd09fc144f932d18a66638cf5bb683d4a76ca0d38943474f7",
        "339578ac2087bbdb497644faf80d3f7ace56d0c9f60c86ffb940840d4a964ad6",
                      "94017c67ed50785e4c739eb40fb064ce2305efc75fe06ea1cea7e7409b3f2e91"},
    "wrapper_design": {"0b7340f3ee69cbe7160f80ba51c4fa93dfb94fadf32e7518ee62b4975f948c2a",
                       "062dd7634ace2214055e5bcb90218043bcd5f142da5a0d70fb99090d26c10a62"},
    "orchestrator_design": {"b9c2783455dc39bc8904b8fd16af498334936d61aed89178b5ced9891c2f8b72", "612c42c0d2c3afb0c7f48fa964a9ca9bc81d542241bb2280ca5b61b55d06bb14",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "681cfb1c03349c7f6edfd3852b9c303f464e4375525857e4edb71e0dd677ad49",
        "fec9b1a759bfbb513210a58472431041d014335317139309281fd8546c42b34a",
                            "44b0788e38d118d0b1a4a7cf2de98d2dbcf3bee334a7885bc8ce8788bac6c365",
                            "35da265d3395dafb7b16aaa1477ff1c3fba106c2c0bc11ca340c8f05da063b62",
                            "81cf9ffa985d209c21d9d32644ff19055335c8fbb6b7941d140ccae39c05d133",
        "8ee1b3ec61e2994c53791f681523bed3f44c8861ae4576a7cd6c01a9b509bfe7",
                              "b1175e5e93c69bb615256ce4684be01648dd7736bee4955052bbca5be914d9dc",
                            "f9e96fafed18fe73fe6e62b9d75184fe99b3c6bdf802d190fed21afb42c35542",
                            "95e1470cf6cff461d1caf1d4e94205c0229b696ee8c852c6bd3acdb7b257a79e",
                            "3e283b3f86a097de59173a64e5068d92ee6fc631799dfaac7c2617f903a91dc9",
        "d6dc2dbe5ee11320b29122983c1d5cf80aa47f47f9346c6430a0457792c30da5"},
    "linked_domain_contracts": {"44cbd09366c9f87a58eac93a64db844b015010fdee434e24573301ab600fe3be",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "b322ba1eb920617a7ca19d7565e2685c5e7695ccd997fddf9559396a1a4b0843",  # before the integration and event-handler modules
        "082994b819661585759f03856a7188552dd1aec9960aed6d63413ce956b4b5bc",
                                "2f1fde8befb5a2d452aea0a989261f084433a5465e64dced1eb3205a53654609",
        "5d19465bca4616a7f4a6e3581fbadcd30b9707efb9c43e993ddddd96ed8cc464",
        "5ef353e2455182ccc268bfd59999b18449c873a8bb41be559aee177df7c6d941",
                                "7373d95b05eadcacff8638d41bc2da63c8627647d323f824a12159828a41f3d7",
                                "f769606aba568416e0e3892f88a6fd147ab3eeb1ff082d9be446ec6ea3fc9dbf",
                                "7a589e02ca2bb06072c304f2f0d99e47482f4eba4d3a7846a4986cfbaef45a8a"},
    "integration_design": {"232d9b4f0e40dd9a43bd5d7d48bf9e8904a73227ec2f093750bd4d1ed7257cb4",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "de5041e39759b2d42c8a5a01034f82a3f0ad7b6914ea4e30465f65d4525af132",
        "0bc173e30220ab4a146c8d55d3c3e7eb228a16a6b1b75fabcaf6bbceb720576f",
        "e8dda7fd040ae6b01935f0b2228207308b0ee8d9cfb67e0800de29597f07425c",
        "17fb32be72076d00361fca9333d38ee203dc223b996db012f277e13b44c8b4e5",
                           "11231d4efa53ca45a9e53af4e52a3666c5258092e03dff6ae17f948ca462ab6d",
        "50062f4c3b3dcc7aab9e4ace195d0134977a341a4a81a22518962ed7f87474c2",
        "22b0c4621eb6a97503c878794caed382350840e41c8ee9dd174b6338d7674bf7",
                              "b53ca7a0466106e58ce2f99daf90e85f88ca28309d8ec35f2a3c0d49186527e5"},
    "independent_tests": {"ddd95b6ab52eb4cebb528d15803c87ecc959554442107474cea0a93273681ca2",
        "b3d893fc941bf04489936346bce2e3e2532dd1bdb30c633f8695f1594aac9d62",
        "10597691f0c0608c4b73927a597d8a9e39fdbd6997549ceb0b35976375a005da",
                          "8a7b319eb255ecea335e60322b4e11c207a2c23e3e698999b749ea81f442b27a",
                          "97d564115dc60807860254fee3ecfa541ca999e29cf9870d487a5efe93031f02",
                          "4b1b34967d2944c200b2b4bd8c0a81768cd6292da94cac47c000f9ba30d0de8e",
                          "d36bccc1f7537976e2b700ce1d597efa5283e7e435cd2aca25aaac50ef889a47",
                          "12aea52ce65e461164115980768436ac4e4a069abee71e9ff4a2f2bde6cfc548"},
    "microservice_scaffold": {"fb302d1bbd3f8aaee80eaff3c488e58a7ae9efe1f0a1dbf6dcde0e790e406073",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "ebc2bde4119fea34214581b5466d2fedd35b55f5044693a0a88d16eb94f629e1",  # before the integration and event-handler modules
        "7407fa4ef1bde3e716e5185935ee4da564f692144d68f54cff9edee0243772e6",
        "184ef7ecf2a671727e17c0d65b19b82993b3d61026433fdea9360a5126291614",
        "d10bb3879c92380cc05bd23f1c91a377749a367067c79bb40d4ad7c4733530e9",
                              "433fa24641180e7ee1a61b9c122707e5088afd85fcfff8238ad9953a37aafe56",
                              "181fe29546e7075f3e007a79554c5c168620b9e23364fea592f1aea97673972c",
                              "775065ebf6599df3036965a91a20399a3bc78702ef5ac7ae7f8b1a107ed59c21",
                              "869be47dce9dcbb7f70c95cf4c4ab19d00772b4b0c5851c71c9e23c481319145",
                              "c2ad8fdcb1f1c9011741b780496fed42b80971e5d67bd0ba1c8a1d1402270ba1",
        "9159384ab952c67a5c905e0f23f2c42d52c30c8e859b4eabe8a3e41090442b56",
        "7356c75d8d7aa56e00af010e64538773939c378a956da6fa58aada1ea347db38",
        "0c42c2258509f9ae8614d72c2986d867f5b7b15aaf6abad4b819fd43f24d24a5",
        "00c115ab87a501e11c920f31e31fae478abe33d069eaeb88c0d031965a252a17",
        "8f9d717d388659e72ae0fe70fe89fd704a75dd65b2f9a9448df57f32e074f79a",
        "a1d026bc8b5619c5000bc18beea1da47643184820b8b845dfc80e6c1ba7c42ea",
                              "7b1cad20a4d8c306b309b9b3a689af000aa7562611a175dac76b108f11dfa7aa"},
    "modernise": {"f5199989f39ae16646acbf79d9c246d424fb9d507aeadc12217647c63b70aa41",
                  "3a97d57409eb272a0311e1bc55d47da3fa1ccf655e303b135937d7e3f8734366",
                  "a0185bf6df97033362944d60bf67817711ba448fda661677bec19d9f87f5a457",
        "5ac003eeae552c2dacdb8f2f3a72032f11e58c6bb440baa2f77090b03a51a6de",
        "ccd29214c9dee61ede975b3af6c32d21f35aa1c7c504e9fbe9a5162dbf1786b1",
        "3fdbd40d27cf56a744c4dcc0cc874d058243279094fb3aabf3f9ca725a0c492a",
        "acf98d8971c8430231b71d5a41e44f92e9ffd9824bdd8500769936b7ae934cac",
        "b16faecb3264355335f347ecdff807fa3b96979e2f4f880e47a200e3a48982a5"},
    "modular_monolith": {"c424e90a2f1a59c3fac732f1828670aadf362bc7eb20d6a561d150b1cc3eabbb",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "dba97de3ae2d5f99c1f4de6d53112580f8fdbb699366d88a5f9561955bb47e9b",
        "0b2f87692a0161118f228703f72775d4df27f3d904a0646e04db7d36b3ad0388",
        "db223089cf2602d7762ed350e887a5e62e581bb1565dada1fb2862f7c3c54ae2",
        "86499680a4b1d9ac6ca5cc3ff6b6c46b2ea084a85aed2ab0b21a16a7dcf88e07",
        "ae6adcee76691ce042b38a21231c497ea4ff3dcf880fa94905f5c0098571bfde",
                         "496c048b744624038c78d0318f8576b486700d5936d2a2b60215dde6e2bdab5a",
                         "07718da8f27f714d734e7edbbad7067e8df6bc5191426931c2b2f8a316ff1dee",
                         "a2faa355ff82f2ded46ea8c43c9f551c16ff2e09a9f5ae0af29931bfac559025",
                              "31d5f8f0bbaa53df1978c04257e99924dade1b2b41f230a985b8bd08830d9adc",
                         "9bf4007e9337fafdab89a901c912245fcd3d44a29989b7732603c7fcada2129b",
                         "164e46fabe89f188ecce8525da9ebda4b9fea4d35b97992f92542414da55c7b9",
                         "db8c731241fbb0878e4b93b12357d93ff4cd2f2160b350346523dcdd883e9765",
        "96dfbf2199cd58075a513f4725fd874e7e8c9affec24e412e666dc32a02e38a8",
        "bf380585492e901711e22a95479416a47e226cbcb962a457bd4c74fca23d02b0",
        "018556caac91c817f3094eb211d8cf971a0d33e110f92bf2741b2a7cf922c134",
        "1ef74b0a61e385d549568080a9464b1330314e6957b8819114716b1d2cb4a52a",
        "5fa33af0b039ab07f2cb732abc0a97fad8bac30609f1aa366c42433f3900cb80",
        "c606bd6e69de66270b01248c20cd160602c92fafc5d0682783ae5998c34c825f",
        "0183c418952e37bbe42453d01ee323819fdaee85b694321e87a6bc36a4e6bc69",
        "991b17db193a33d5329c3b2957f6fb182c7f9b557f25d48d97977435a74fb199",
                         "20369f074da4f5870c38bdea7c129dc92217a37a182b7e3dba3c983e3a226a09",
                         "c3a678317498d3b0682c70f3a3b41727037b401f23c54713108a735babbd28fa",
                         "8d67e4fd44f4bce07fa5d8aa0d6658c52c4bb703100914bc538267e060258dac"},
    "monolith_integration": {"cc1db63c28cf8496c03081e521cd0ea3102ec1e375f5265060a0940575d31ebb", "b5aac4030a9aee5307f4d8ac17866b2a25517962e09ae1be67c6b3baaf8f23ae",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "5bc76089ab268f53fff05ec6f0123a72908f3ecabfd8bfb91a7842166fbc1ac6",  # v37, before the entry-point and handler names were spelled out
        "00946d501dc1074cd5db3e24cc07d23fe060ca16b87610ca6c2661515dd60ab8",
        "133635292b0946704b5ed7ea641316940df360df236815da635ae1ade410e408",
        "6a03b6553fc4062747ffdd63058c818020acb607946c9d1e0c1805465594e8b3",
        "2599df41784b8e9212b94ab2de016496db7308f2323635ed380de312556fa57a",
        "b41c0cbf33c34303d4822e5c32ece71077a54659ec51cc621870a6e015eb4eaf",
                             "29b5cee315c086c998bba657793a71713eb7f46bf71a98b67fcff29efaa32738",
                             "11fddb537cfb88b83b055c2c32163d278f4d1aa29087d8b9cea2b11a428fd930",
                             "686fec3600f472af2216b6e1805795367b9dd761933e094e3bf424dfbbf8d6b1",
                             "08a60fa01d2f984772a8098e024a65cf5d680316227473e39125c87c0ac8b791",
        "b82822208202ff2dfd10ff0e77918503973178a46c10673641e4a95fad988f2c",
        "e031ab929248d9c9b40ade7b6f9b782f099ad0daefaa39db19e50624a7722887",
        "7a7e4b31d4c37c57d308e9d6e371dc89808055ec284cadc51ad698532f23945e",
                             "673a0969f74f104d418d47b8e935f34ca65c6eeeca498425883cdda80006894b"},
    "as_is_doc": {"9fb0392d1747e6a478a3e0cd6d77ea8cd4564fd9e815959b5baf2f48ee52c892"},
    "bounded_contexts": {"0fe37358d0947d3a11a0fc7d5440eb9ebaa72ec9171caff82a4612112c073294"},
    "monolith_orchestrator": {"d27c00ce7a828ae72eeeb6d3eccb1a1505557676bd82c41b0d659f81336e449d", "9b9b02478b8cb78f14a09d7350b605a8cd3069e8e3bef8b0ed4e30f350fcbddc",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "9413d856b7092c5e63d5153534afe28bf8c26c63030d65e6fa380b7517087a3f",
        "aa5ab86edeb3922fd89adbd4218d41bcf5b07872686d447e7daa7ae0aa46644d",
        "d7c5287b7420e74a8fe380f2f3a171e4e3f9802deb70550bbcd7fd176a3b50fb",
        "b657db126d0c4c1e1c89547fb8a15d33b835cb1d6a0f672ef3349d37a50d2288",
        "35a24835d71b95dc27b3516c23305bb5ba7eb9525e031f7931aa5d1fb4af382e",
        "cae3d71b1aea57fadae64e5ddcf348386475d4f6890dc311606ccbf486e96e47",
        "74d58902f2f8850ab0f40a8ae931b69d0d12745d6b21071d848463d786cfe630",
        "f8d6c72fa43ec08d5245ad37fcd0be1e83d52c32ea37296345ef2751a6fd496b",
        "2e6d1a954a8a63a21f10dbd5bdeec4af00bea1ee01a10142ada6f57fca975129",
        "5b1954381befa4b650c27376281f54dcdd5e53b1b8b844eb308f315dd59e632a",
        "3179eeac8cf303ea388310a0c0c6c950c414a5aef1f1e9ed7101b39da4232903",
                              "17eb8df57a93ef73c26dc2eb09ff90403eb57b0c52f8cbfde79a2fba511fec64"},
    "orchestrator_service": {"2bae4f7b006c71220e8b929c8664dafb6439d81ad7bc73196ed5f4b98a7e9d7e",  # v37, before the entry-point and handler names were spelled out
        "ec5d9fb2b9387f2daa0b048e935edde54c88234663a80d5f32706a823f1eceb2",  # before the integration and event-handler modules
        "7319dc8a36c96a8cee660ebc44e92a8e3493d778666885d6363032b27c02a68b",
        "45b299890d3b03c87327a570a46a7a3aca76641318fbd2afc087693924f0acfd",
        "84da51f2092f27ab9f264d98062fd5444fa6b35e27b63cb83fed187c824a6fee",
        "49bee293f5d1a39bbaa8014822dfe74319a13645fa2c8bc89bdc3b32185f1e68",
        "e9a0a4f846d21ff3e0b360c6acd0d6017f453bfe9dc5c5ea937669b2c49c9be9",
                             "60e428368d945638916a3fd4a8fe75479eeca06e59a4cb3eed65fc861021c2ed"},
    "modulith_design": {"bb58a07466e678bcd0e6a943be48a4c4ddb27769e9771dba1c49f74b0adf4a1a", "10e717d44ed190c757435b1b3847d030e0680799b3c45f30e2fc89a6f296a926", "67eb1acd9d20b6e7f3beb577610d2daeb5f6d9980a782fcf8145b09dd5f23780",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "ff034a9939629786a8035913a5955c679fc486e1a85a4d9025cb7cba9f4177c1",
        "a8c53e3d530620c70745c1acaa65dccbb77258d932faf7707670ce728aefe67f",
        "0265edb02c4e0185a7079115bfbdb789cade24afe450f06778e2d8d251bdd597",
        "ca24f0f89fa6386a8562748c376199366381dab8c59adb1e7981b41851a779d7",
        "9e1f48da5f5a2658cde5e7f7ad261fa0fb253d4ab49590d8c17f66ce61f5162d",
        "71579eb23690ceaebc0428cedfd0c53fd25330676724c94f28f519d26ebcf93a",
        "252cf4ec3774bac5dc90c8853ec400f96308cd88864ddc7f2d0d6c241e190c54",
        "da774e69420c666793587dc7f0e433e7a06e56c3f3ca5bf203e672cc98a53739",
                        "3082df6c03384325fd257008848ad68ee4420b1bbf92388e51f2901161690271"},
    "service_tests": {"92748bd2b1cb6f490d603714dc95bd9067baead0f79409ac43927cf7a1f81149"},
    "modernise_and_test": {"b03b62d6f6e2e2d003169e95cf1fb52c1d1a108cc6057cb18fa454fa95cb6f93",
        "e7bc5c0262d9b6c238b365b12635a74126853cd5da96ed091f23201cc2a28a73",
        "f344e3a8168e60d7ce6aff5aaaafe61ef51c0687c994261490c708af0cb135cf",
        "b440b328d64fa7626d422669ac339a160cc813275ebb26c519aef5f9ad73cb6f",
        "a33d06bbba111d533ffd3973d9226f06ad3f072e13cf06e19ad366352edd7bdd",
                           "3017cc168fb1c98e83afe8bcf0f31cd82894893a7fd92d47d909d523e49f71d8"},
    "dependency_analysis": {"3ff84e984a5bdaebac0fa7074694e89f829372c906cda23df902864c66209184",  # defaults of commits 6ab0db8 / 1bddc72 and earlier
        "9065f34f204858e38b0ab566908d0857ab743947350df51a1ceda6e67c5b5815",
        "ba664201046396f05dd167c9996bd3a331c1b3b6365db7f2af0d11c3d56e0254"},  # added in this release: no earlier default
    "orchestrator_tests": {"59931983dacdd34d87b17875f1882ba5ba60a68c477f0cec80386bcf4360bfe6"},  # added in this release: no earlier default
}
# Built-ins whose step now needs more turns than before: an upgrade raises (never lowers) their limit.
MORE_TURNS = {"modular_monolith", "microservice_scaffold"}
UPGRADE_MARKER = "_upgraded_builtin_defaults_v38"  # v37-38: the integration and event-handler modules
GUARDRAILS_MARKER = "_builtin_guardrails_v1"  # built-ins got their guardrail tools and checks (_add_guardrails)
GUARDRAILS_MOVED_MARKER = "_guardrails_moved_to_agents"  # global and project guardrails became agents' own
GUARDRAILS_OUT_MARKER = "_guardrails_out_of_instructions"  # built-ins' guardrails moved from instructions to Guardrails
# Built-in agents the Standard migration workflow no longer uses. They stay in the code and the database, and keep
# working in custom workflows and runs that use them, but are not listed on the Agents page or offered in the workflow
# editor unless a workflow uses them.
HIDDEN = tuple(stage.key for stage in SUPERSEDED)
BUILTIN = tuple(BUILTIN_STAGES)


def _as_dict(row: Agent, customised: bool = False) -> dict[str, Any]:
    return {"key": row.key, **{f: getattr(row, f) for f in FIELDS}, "checks": row.checks or [],
            "guardrails": row.guardrails or "",
            "builtin": row.key in BUILTIN, "scope": "shared" if row.project == SHARED else "project",
            "customised": customised, "release_status": release_status(row),
            "created_at": row.created_at, "updated_at": row.updated_at}


# What a built-in agent is compared with this release's default on: what a release sets (mode, model, effort, JDK and
# turns are the user's choices and never part of a release's change).
RELEASE_FIELDS = ("instructions", "outputs", "tools", "checks", "guardrails")


def _release_value(field: str, value: Any) -> Any:
    if field in ("outputs", "tools", "checks"):
        return list(value or [])
    return value or ""


def release_status(row: Agent) -> str | None:
    """How a built-in agent stands against this release's default: `current` (everything a release sets is the
    default), `older-default` (instructions an earlier release gave that nobody edited: the next start upgrades them),
    or `edited` (changed by someone: a release never overwrites it). None for a custom agent (no default)."""
    if row.key not in BUILTIN:
        return None
    default = default_config(row.key)
    if all(_release_value(f, getattr(row, f)) == _release_value(f, default[f]) for f in RELEASE_FIELDS):
        return "current"
    if hashlib.sha256((row.instructions or "").encode()).hexdigest() in PREVIOUS_DEFAULT_HASHES.get(row.key, set()):
        return "older-default"
    return "edited"


def compare_with_default(key: str, project: str | None = None) -> dict[str, Any]:
    """The agent the project uses against this release's default, field by field (lists one item per line), for the
    Agents page's comparison. KeyError when unknown; ValueError for a custom agent (it has no default)."""
    project = _project(project)
    with session_scope() as s:
        row = _effective(s, key, project)
        if row is None:
            raise KeyError(key)
        if key not in BUILTIN:
            raise ValueError("Only built-in agents have a default to compare with")
        default = default_config(key)
        fields = []
        for f in RELEASE_FIELDS:
            mine, theirs = _release_value(f, getattr(row, f)), _release_value(f, default[f])
            text = (lambda v: "\n".join(v)) if isinstance(mine, list) else (lambda v: v)
            fields.append({"field": f, "stored": text(mine), "default": text(theirs), "same": mine == theirs})
        return {"key": key, "title": row.title, "status": release_status(row),
                "scope": "shared" if row.project == SHARED else "project", "updated_at": row.updated_at,
                "fields": fields}


def builtin_default(key: str) -> dict[str, Any]:
    stage = BUILTIN_STAGES[key]
    return {"title": stage.title, "description": stage.description, **default_config(key)}


def custom_default(key: str, title: str, description: str) -> dict[str, Any]:
    return {
        "title": title, "description": description, "mode": "stub", "model": default_config("deep_dive")["model"],
        "effort": "high", "jdk": "legacy", "tools": list(DOCS), "outputs": [f"{key}.md"], "max_turns": 80, "checks": [],
        "guardrails": "", "instructions": f"You are the {title} agent.\n\nDescribe what this agent should do, which code and documents "
                        "it should read, and what it must produce.",
    }


def _legacy_configs() -> dict[str, dict]:
    """Per-stage configs saved before agents became a library (table `agent_configs`)."""
    engine = get_engine()
    if not inspect(engine).has_table("agent_configs"):
        return {}
    with engine.connect() as conn:
        return {row["stage"]: dict(row) for row in conn.execute(text("SELECT * FROM agent_configs")).mappings()}


def seed() -> None:
    """On first start, create the built-in agents, keeping choices saved under older versions."""
    with session_scope() as s:
        if s.scalar(select(Agent).limit(1)) is not None:
            _models_to_auto(s)
            _add_later_builtins(s)
            _upgrade_untouched_builtins(s)
            _give_documents_agents_edit_file(s)
            _update_untouched_descriptions(s)
            _warn_outdated_analysis(s)
            _add_guardrails(s)
            _move_guardrails_to_agents(s)
            _move_guardrails_out_of_instructions(s)
            _remember_untouched_guardrails(s)
            return
        for marker in [*LATER_BUILTINS, UPGRADE_MARKER, GUARDRAILS_MARKER, EDIT_FILE_MARKER]:  # a fresh database: the current built-ins
            s.add(Setting(key=marker, value=True))
        legacy = _legacy_configs()
        old_settings = {row.key: row.value for row in s.query(Setting).filter(
            Setting.project == GLOBAL, Setting.key.in_(["agent_mode", "agent_models", "agent_effort"]))}
        for key in BUILTIN:
            config = builtin_default(key)
            if key in legacy:
                config.update({f: legacy[key][f] for f in FIELDS if f in legacy[key] and legacy[key][f] is not None})
                for f in ("tools", "outputs"):  # stored as JSON text by the raw query
                    if isinstance(config[f], str):
                        config[f] = json.loads(config[f])
            else:
                if "agent_mode" in old_settings:
                    config["mode"] = old_settings["agent_mode"]
                if key in (old_settings.get("agent_models") or {}):
                    config["model"] = old_settings["agent_models"][key]
                if "agent_effort" in old_settings:
                    config["effort"] = old_settings["agent_effort"]
            s.add(Agent(project=SHARED, key=key, **config))
        s.flush()
        _move_guardrails_to_agents(s)
        _move_guardrails_out_of_instructions(s)  # settings saved under older versions may hold them


def _changes_code(row: Agent) -> bool:
    """An agent that can change code: the kind the global and project guardrails were given to. Built-in documents
    agents have edit_file too (to edit their documents in place), but change no code."""
    from app.agents.defaults import _ANALYSIS

    tools = set(row.tools or [])
    return "run_command" in tools or ("edit_file" in tools and row.key not in _ANALYSIS)


def _joined(*texts: str | None) -> str:
    return "\n\n".join(t.strip() for t in texts if t and t.strip())


def _move_guardrails_to_agents(s) -> None:
    """Once: guardrails are kept per agent only. The global guardrails (Agents page) and each project's own (Project
    settings, added to the global ones or in their place) went to every agent whose step changed code; their text is
    moved into the guardrails of each agent that can change code, so every agent keeps the rules it was given:
    the global text into the shared agents (and projects' copies), a project's text into that project's copies (made
    where the project had none). The old settings are then removed."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_MOVED_MARKER)) is not None:
        return
    keys = ("custom_guardrails", "project_guardrails", "project_guardrails_mode")
    rows = list(s.scalars(select(Setting).where(Setting.key.in_(keys))))
    stored = {(r.project, r.key): r.value for r in rows}
    shared_text = str(stored.get((GLOBAL, "custom_guardrails")) or "")
    own = {p: (str(stored.get((p, "project_guardrails")) or ""), stored.get((p, "project_guardrails_mode")) or "add")
           for p, k in stored if k == "project_guardrails" and p != GLOBAL}

    def text_for(project: str, current: str) -> str:
        project_text, mode = own.get(project, ("", "add"))
        if not project_text.strip():
            return _joined(current, shared_text)
        return _joined(current, project_text) if mode == "replace" else _joined(current, shared_text, project_text)

    agents = list(s.scalars(select(Agent)))
    have = {(a.project, a.key) for a in agents}
    coders = [a for a in agents if _changes_code(a)]
    # A project with guardrails of its own gets a copy of each shared agent that changes code, made from the shared
    # agent as it is before the move (its own guardrails only), so the copy's text is the project's effective one.
    copies = [Agent(project=project, key=shared.key, **{f: getattr(shared, f) for f in FIELDS})
              for project, (project_text, _mode) in own.items() if project_text.strip()
              for shared in coders if shared.project == SHARED and (project, shared.key) not in have]
    for row in [*coders, *copies]:
        row.guardrails = _joined(row.guardrails, shared_text) if row.project == SHARED else \
            text_for(row.project, row.guardrails)
    s.add_all(copies)
    for r in rows:
        s.delete(r)
    s.add(Setting(key=GUARDRAILS_MOVED_MARKER, value=True))


def _move_guardrails_out_of_instructions(s) -> None:
    """Once: built-in agents' guardrails (the fixed rules, the Modulith guardrails, the step's hard rules) moved from
    their instructions to their Guardrails. In every copy of a built-in agent, shared or a project's, each guardrail
    section still in the instructions word for word is taken out and put first in its guardrails, so an agent edited
    elsewhere moves too; a section someone changed stays where it is."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_OUT_MARKER)) is not None:
        return
    from app.agents.defaults import GUARDRAIL_SECTION_TEMPLATES, GUARDRAIL_SECTIONS, without

    for row in s.scalars(select(Agent).where(Agent.key.in_(list(GUARDRAIL_SECTIONS)))):
        text = row.instructions or ""
        moved = []
        for stored, written in zip(GUARDRAIL_SECTION_TEMPLATES[row.key], GUARDRAIL_SECTIONS[row.key]):
            # As stored (shared texts as placeholders), or written out where instructions never got placeholders.
            if stored in text:
                moved.append(stored)
            elif written in text:
                moved.append(written)
        if moved:
            row.instructions = without(text, moved)
            row.guardrails = _joined(*moved, row.guardrails)
    s.add(Setting(key=GUARDRAILS_OUT_MARKER, value=True))


def _add_later_builtins(s) -> None:
    """Once per release: add built-in agents introduced since the database was created. A built-in the user deleted
    afterwards is not re-added."""
    for marker, keys in LATER_BUILTINS.items():
        if s.get(Setting, (GLOBAL, marker)) is not None:
            continue
        for key in keys:
            if s.get(Agent, (SHARED, key)) is None:
                s.add(Agent(project=SHARED, key=key, **_with_settings_of(s, SHARED, key, builtin_default(key))))
            source = LATER_SETTINGS_FROM.get(key)
            if source:  # projects with their own copy of the agent it takes over get their own copy of it too
                for copy in s.scalars(select(Agent).where(Agent.key == source, Agent.project != SHARED)):
                    if s.get(Agent, (copy.project, key)) is None:
                        s.add(Agent(project=copy.project, key=key,
                                    **_with_settings_of(s, copy.project, key, builtin_default(key))))
        s.add(Setting(key=marker, value=True))


def _with_settings_of(s, project: str, key: str, config: dict) -> dict:
    """A later built-in's config with the choices of the agent whose work it takes over (LATER_SETTINGS_FROM)."""
    source = LATER_SETTINGS_FROM.get(key)
    row = s.get(Agent, (project, source)) if source else None
    if row is None:
        return config
    return {**config, **{f: getattr(row, f) for f in ("mode", "model", "effort") if getattr(row, f)}}


def outdated_analysis(row: Agent | None) -> bool:
    """An Analysis agent that doesn't write the functional specification: its instructions were edited before the
    release that added it (an edited agent isn't upgraded), or a project's own copy (copies never are)."""
    return row is not None and "functional-spec.md" not in (row.outputs or [])


def _warn_outdated_analysis(s) -> None:
    """At every start, while it lasts: the lean workflow's Domain dependencies step now finds the dependencies and
    Analysis is expected to write the functional specification, which an outdated Analysis agent doesn't."""
    rows = [r for r in s.scalars(select(Agent).where(Agent.key == "analysis")) if outdated_analysis(r)]
    if rows:
        where = ", ".join("shared" if r.project == SHARED else f"project {r.project}" for r in rows)
        log.warning("The Analysis agent (%s) writes no functional-spec.md: it was edited before this release, so it "
                    "wasn't upgraded. Lean runs then have no functional specification, and its dependency work is "
                    "done twice (the Domain dependencies step does it too). Reset it on the Agents page, or add the "
                    "functional specification to its instructions and required documents.", where)


# Descriptions of built-ins as earlier releases gave them: one still word for word (shared or a project's copy) gets
# the current description; an edited one is kept. Releases don't otherwise change a stored description.
PREVIOUS_DESCRIPTIONS = {
    "independent_tests": ("A black-box acceptance suite for every scenario, in its own module of the domain service's "
                          "repository: it calls the running service over HTTP, so it runs unchanged before and after "
                          "modernisation."),
}


def _update_untouched_descriptions(s) -> None:
    for key, old in PREVIOUS_DESCRIPTIONS.items():
        current = BUILTIN_STAGES[key].description
        for row in s.scalars(select(Agent).where(Agent.key == key)):  # compared here: a text column (Oracle CLOB)
            if row.description == old:
                _keep_updated_at(row)  # a release's wording, not a person's edit
                row.description = current


EDIT_FILE_MARKER = "_documents_agents_edit_file"


def _give_documents_agents_edit_file(s) -> None:
    """Once: documents agents edit a document in place (an amendment, a re-run), so each built-in one gets edit_file,
    edited or not (a tool added, nothing removed)."""
    from app.agents.defaults import _ANALYSIS

    if s.get(Setting, (GLOBAL, EDIT_FILE_MARKER)) is not None:
        return
    for row in s.scalars(select(Agent).where(Agent.key.in_(sorted(_ANALYSIS)))):
        tools = list(row.tools or [])
        if "write_file" in tools and "edit_file" not in tools:
            _keep_updated_at(row)
            row.tools = [*tools, "edit_file"]
    s.add(Setting(key=EDIT_FILE_MARKER, value=True))


def _guardrails_key(key: str) -> str:
    return f"_default_guardrails:{key}"


def remember_default_guardrails(s, key: str, text: str) -> None:
    """Note the default guardrails the studio gave a built-in agent: while they stay exactly these, a release may
    bring them up to its default; once someone edits them, they are left alone."""
    s.merge(Setting(project=GLOBAL, key=_guardrails_key(key), value=hashlib.sha256((text or "").encode()).hexdigest()))


def _upgrade_untouched_builtins(s) -> None:
    """Once per release: built-ins whose instructions nobody edited get the current defaults, without losing what
    people added. Edited instructions: the agent is left alone. Otherwise the instructions and the documents they
    require become the defaults; tools and checks get the new defaults added (never removed); the guardrails become the
    default only while they are empty or still the default the studio gave (an edit keeps them as they are). Mode,
    model, effort, JDK and max turns are kept. What happened to each agent is logged (startup report)."""
    if s.get(Setting, (GLOBAL, UPGRADE_MARKER)) is not None:
        return
    upgraded, kept, guardrails_kept = [], [], []
    for key, hashes in PREVIOUS_DEFAULT_HASHES.items():
        row = s.get(Agent, (SHARED, key))
        if row is None:
            continue
        current = default_config(key)
        if row.instructions == current["instructions"]:
            continue  # already this release's
        if hashlib.sha256(row.instructions.encode()).hexdigest() not in hashes:
            kept.append(row.title)
            continue
        _keep_updated_at(row)  # the studio's upgrade, not a person's edit: runs don't report the agent as changed
        row.instructions, row.outputs = current["instructions"], current["outputs"]  # the documents they ask for
        row.tools = [*row.tools, *(t for t in current["tools"] if t not in row.tools)]
        row.checks = [*(row.checks or []), *(c for c in current["checks"] if c not in (row.checks or []))]
        stored = row.guardrails or ""
        known = s.get(Setting, (GLOBAL, _guardrails_key(key)))
        untouched = (not stored.strip() or stored == current["guardrails"]
                     or (known is not None and hashlib.sha256(stored.encode()).hexdigest() == known.value))
        if untouched:
            row.guardrails = current["guardrails"]
            remember_default_guardrails(s, key, row.guardrails)
        elif stored != current["guardrails"]:
            guardrails_kept.append(row.title)
        if key in MORE_TURNS:
            row.max_turns = max(row.max_turns, current["max_turns"])
        upgraded.append(row.title)
    s.add(Setting(key=UPGRADE_MARKER, value=True))
    if upgraded:
        log.info("Built-in agents upgraded to this release's instructions: %s", ", ".join(upgraded))
    if kept:
        log.info("Built-in agents kept as they are (their instructions were edited; the defaults changed): %s",
                 ", ".join(kept))
    if guardrails_kept:
        log.info("Guardrails kept as edited (a newer default is available; Reset on the agent's page takes it): %s",
                 ", ".join(guardrails_kept))


def _keep_updated_at(row) -> None:
    """Keep the row's `updated_at` through this change: it is when a person last edited it (runs compare it with when
    their steps ran), and a release bringing a built-in up to its defaults is not such an edit."""
    from sqlalchemy.orm.attributes import flag_modified

    row.updated_at = row.updated_at
    flag_modified(row, "updated_at")  # written as it is, so the column's onupdate doesn't stamp the upgrade's time


def _remember_untouched_guardrails(s) -> None:
    """Each start: a built-in whose guardrails are exactly the current default (new, reset, or never edited) has that
    default noted, so the next release can bring them up to its own."""
    for key in BUILTIN:
        row = s.get(Agent, (SHARED, key))
        if row is not None and row.guardrails and row.guardrails == default_config(key)["guardrails"]:
            known = s.get(Setting, (GLOBAL, _guardrails_key(key)))
            digest = hashlib.sha256(row.guardrails.encode()).hexdigest()
            if known is None or known.value != digest:
                remember_default_guardrails(s, key, row.guardrails)


def _add_guardrails(s) -> None:
    """Once per release: give every built-in agent its current guardrail tools and checks, edited or not (only added,
    never removed). The checks are enforced anyway (common.required_checks); the tools let the agent check itself."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_MARKER)) is not None:
        return
    for row in s.scalars(select(Agent).where(Agent.key.in_(BUILTIN))):
        current = default_config(row.key)
        guard = [t for t in current["tools"] if t.startswith("check_")]
        row.tools = [*row.tools, *(t for t in guard if t not in row.tools)]
        row.checks = [*(row.checks or []), *(c for c in current["checks"] if c not in (row.checks or []))]
    s.add(Setting(key=GUARDRAILS_MARKER, value=True))


def _models_to_auto(s) -> None:
    """Once: agents created with the old Claude-only defaults let the AI provider choose instead, since Copilot
    model ids differ."""
    if s.get(Setting, (GLOBAL, "_migrated_models_to_auto")) is not None:
        return
    for agent in s.scalars(select(Agent).where(Agent.model.in_(CLAUDE_MODELS))):
        agent.model = AUTO
    s.add(Setting(key="_migrated_models_to_auto", value=True))


def _project(project: str | None) -> str | None:
    return project or projects.current_or_none()


def _effective(s, key: str, project: str | None) -> Agent | None:
    """The project's copy of an agent, or the shared one."""
    return (s.get(Agent, (project, key)) if project else None) or s.get(Agent, (SHARED, key))


def list_all(project: str | None = None, include_hidden: bool = False) -> list[dict[str, Any]]:
    """The agents a project uses: shared ones, replaced by the project's copies, plus its own. Superseded built-ins
    (HIDDEN) are left out unless a workflow still uses them or `include_hidden` is set."""
    project = _project(project)
    return [_with_status(a) for a in cache.cached("agents", ("agents", "workflows"), (project, include_hidden),
                                                  lambda: _list_all(project, include_hidden))]


def _with_status(agent: dict[str, Any]) -> dict[str, Any]:
    """The agent's release status, worked out when it is read (from the release's defaults in the code, which a cached
    copy of the row doesn't follow)."""
    agent["release_status"] = release_status(SimpleNamespace(**agent))
    return agent


def _list_all(project: str | None, include_hidden: bool) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = {r.key: r for r in s.scalars(select(Agent).where(Agent.project == SHARED))}
        shared = set(rows)
        if project:
            for r in s.scalars(select(Agent).where(Agent.project == project)):
                rows[r.key] = r
        if not include_hidden:
            used = {step["key"] for w in s.scalars(select(WorkflowDef)) for step in w.steps
                    if step.get("kind") == "agent"}
            rows = {k: r for k, r in rows.items() if k not in HIDDEN or k in used}
        order = {k: i for i, k in enumerate(BUILTIN)}
        ordered = sorted(rows.values(), key=lambda r: (order.get(r.key, len(order)), r.created_at))
        return [_as_dict(r, customised=r.project != SHARED and r.key in shared) for r in ordered]


def get(key: str, project: str | None = None) -> dict[str, Any]:
    project = _project(project)

    def load() -> dict[str, Any] | None:
        with session_scope() as s:
            row = _effective(s, key, project)
            if row is None:
                return None
            return _as_dict(row, customised=row.project != SHARED and s.get(Agent, (SHARED, key)) is not None)

    found = cache.cached("agent", ("agents",), (project, key), load)
    if found is None:
        raise KeyError(key)
    return _with_status(found)


def exists(key: str, project: str | None = None) -> bool:
    with session_scope() as s:
        return _effective(s, key, _project(project)) is not None


def titles(project: str | None = None) -> dict[str, tuple[str, str]]:
    """key -> (title, description) for every agent the project uses, in list_all's order. Reads only those columns:
    every workflow read needs the titles, and the agents' instructions are large."""
    project = _project(project)
    return cache.cached("agent_titles", ("agents",), project, lambda: _titles(project))


def _titles(project: str | None) -> dict[str, tuple[str, str]]:
    with session_scope() as s:
        rows = s.execute(select(Agent.project, Agent.key, Agent.title, Agent.description, Agent.created_at)
                         .where(Agent.project.in_([SHARED, project] if project else [SHARED]))).all()
    effective = {r.key: r for r in rows if r.project == SHARED}
    effective.update({r.key: r for r in rows if r.project != SHARED})  # the project's copies win
    order = {k: i for i, k in enumerate(BUILTIN)}
    ordered = sorted(effective.values(), key=lambda r: (order.get(r.key, len(order)), r.created_at))
    return {r.key: (r.title, r.description or "") for r in ordered}


def _target(scope: str) -> str:
    """Where a change goes: the shared library, or the current project's copies."""
    return SHARED if scope == "shared" else projects.current()


def create(key: str, title: str, description: str, scope: str = "shared") -> dict[str, Any]:
    target = _target(scope)
    with session_scope() as s:
        if s.get(Agent, (SHARED, key)) is not None or s.get(Agent, (target, key)) is not None:
            raise ValueError(f"An agent with key {key} already exists")
        s.add(Agent(project=target, key=key, **custom_default(key, title, description)))
    return get(key)


def update(key: str, values: dict[str, Any], scope: str = "shared") -> dict[str, Any]:
    """Change the shared agent, or the project's copy of it (made from the shared one when there is none yet)."""
    target = _target(scope)
    with session_scope() as s:
        row = s.get(Agent, (target, key))
        if row is None:
            shared = s.get(Agent, (SHARED, key))
            if shared is None:
                raise KeyError(key)
            row = Agent(project=target, key=key, **{f: getattr(shared, f) for f in FIELDS})
            s.add(row)
        for field in FIELDS:
            if field in values:
                setattr(row, field, values[field])
    return get(key)


def usage(key: str, scope: str = "shared") -> dict[str, Any]:
    """What deleting the agent would affect, and why it can't be deleted when it can't:
    {"title", "workflows": [{"name", "library"}] with a step for it, "runs": [...] unfinished runs whose steps include
    it, "copies": projects with their own copy (deleted with the shared agent), "blocked": the reason, or None}.
    A shared agent is used by every project, so every project's workflows and runs count; a project's own agent, only
    the project's."""
    project = projects.current_or_none()
    with session_scope() as s:
        row = s.get(Agent, (_target(scope), key))
        if row is None:
            raise KeyError(key)
        if scope == "shared":
            workflows = [w for w in s.scalars(select(WorkflowDef))]
        else:  # the project's own workflows and copies, and the shared ones it hasn't copied
            mine = {w.key: w for w in s.scalars(select(WorkflowDef).where(WorkflowDef.project == project))}
            workflows = [*mine.values(), *(w for w in s.scalars(select(WorkflowDef).where(WorkflowDef.project == SHARED))
                                          if w.key not in mine)]
        using = [{"name": w.name, "library": "shared" if w.project == SHARED else w.project} for w in workflows
                 if any(st.get("kind") == "agent" and st.get("key") == key for st in w.steps)]
        runs_query = select(Run).where(Run.status != "completed").options(load_only(
            Run.id, Run.project, Run.domain, Run.operation, Run.status, Run.workflow_stages, Run.created_at))
        if scope != "shared":
            runs_query = runs_query.where(Run.project == project)
        runs = [{"id": r.id, "project": r.project, "domain": r.domain, "operation": r.operation, "status": r.status}
                for r in s.scalars(runs_query.order_by(Run.created_at))
                if any(st.get("key") == key for st in r.stage_defs)]
        copies = sorted(a.project for a in s.scalars(select(Agent).where(Agent.key == key, Agent.project != SHARED))) \
            if scope == "shared" else []
        reasons = []
        if using:
            where = ", ".join(w["name"] + (" (shared)" if w["library"] == "shared" else f" (project {w['library']})")
                              for w in using)
            reasons.append(f"it is a step of {len(using)} workflow(s): {where}. Remove its step from each on the "
                           "Workflows page first")
        if runs:
            listed = ", ".join(f"{r['domain']}/{r['operation']} ({r['status'].replace('_', ' ')})" for r in runs[:8])
            more = f" and {len(runs) - 8} more" if len(runs) > 8 else ""
            reasons.append(f"{len(runs)} unfinished run(s) still use it: {listed}{more}. Let them finish, or reset "
                           "their features on the domain's Features tab")
        return {"title": row.title, "workflows": using, "runs": runs, "copies": copies,
                "blocked": "; and ".join(reasons) or None}


def set_mode_all(mode: str, project: str | None = None) -> int:
    """Run every agent the project uses as a stub or with AI (`mode`): the shared library and the project's own agents
    and copies. Returns how many changed."""
    project = _project(project)
    with session_scope() as s:
        rows = s.scalars(select(Agent).where(Agent.project.in_([SHARED, *([project] if project else [])]),
                                             Agent.mode != mode)).all()
        for row in rows:
            row.mode = mode
        return len(rows)


def customise(key: str) -> dict[str, Any]:
    """Give the current project its own copy of a shared agent."""
    return update(key, {}, scope="project")


def revert(key: str) -> dict[str, Any]:
    """Drop the current project's copy, going back to the shared agent."""
    with session_scope() as s:
        if s.get(Agent, (SHARED, key)) is None:
            if s.get(Agent, (projects.current(), key)) is None:
                raise KeyError(key)  # no such agent at all
            raise ValueError("This agent exists only in this project; delete it instead")
        row = s.get(Agent, (projects.current(), key))
        if row is not None:
            s.delete(row)
    return get(key)


def reset(key: str) -> dict[str, Any]:
    """The shared agent back to its built-in default, keeping its mode: a reset never switches an agent that runs the
    AI back to the stub (or the other way round)."""
    if key not in BUILTIN:
        raise ValueError("Only built-in agents have a default to reset to")
    with session_scope() as s:
        row = s.get(Agent, (SHARED, key))
        mode = row.mode if row is not None else None
    default = builtin_default(key)
    return update(key, {**default, "mode": mode or default["mode"]}, scope="shared")


def delete(key: str, scope: str = "shared") -> None:
    with session_scope() as s:
        row = s.get(Agent, (_target(scope), key))
        if row is None:
            raise KeyError(key)
        s.delete(row)
        if scope == "shared":  # project copies of a deleted shared agent go too
            s.query(Agent).filter(Agent.key == key).delete(synchronize_session=False)
