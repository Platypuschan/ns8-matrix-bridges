*** Settings ***
Library    SSHLibrary
Library    String
Library    Collections
Library    OperatingSystem
Test Setup    Stop after a failed step

*** Variables ***
# matrix-whatsapp of the tested build; the other bridges have the same tag
${IMAGE_URL}         ghcr.io/platypuschan/matrix-whatsapp:latest
${PREVIOUS_IMAGE_URL}    ${EMPTY}
${SCENARIO}          install
${HOST}              matrix.dom.test
${SERVER_NAME}       dom.test
${matrix_id}         ${EMPTY}
${admin_token}       ${EMPTY}
&{bridge_ids}
${ADMIN_USER}        admin
${ADMIN_PASSWORD}    Nethesis,1234

*** Keywords ***
Stop after a failed step
    Skip If    '${PREV TEST STATUS}' == 'FAIL'    a previous step failed

Login to cluster-admin
    New Page    https://${NODE_ADDR}/cluster-admin/
    Fill Text    text="Username"    ${ADMIN_USER}
    Click    button >> text="Continue"
    Fill Text    text="Password"    ${ADMIN_PASSWORD}
    Click    button >> text="Log in"
    Wait For Elements State    css=#main-content    visible    timeout=10s

Image of
    [Arguments]    ${key}
    ${image} =    Replace String    ${IMAGE_URL}    /matrix-whatsapp:    /matrix-${key}:
    RETURN    ${image}

Add module
    [Arguments]    ${image}
    ${output}    ${rc} =    Execute Command    add-module ${image} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    add-module ${image} failed: ${output}
    &{output} =    Evaluate    ast.literal_eval(r'''${output}''')    modules=ast
    RETURN    ${output.module_id}

Task
    [Arguments]    ${module}    ${action}    ${payload}={}
    ${output}    ${rc} =    Execute Command    api-cli run module/${module}/${action} --data '${payload}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    ${action} of ${module} failed: ${output}
    ${result} =    Evaluate    json.loads(r'''${output}''') if r'''${output}'''.strip() else {}    modules=json
    RETURN    ${result}

Matrix
    [Arguments]    @{args}    ${token}=${EMPTY}
    ${auth} =    Set Variable If    $token    --token ${token}    ${EMPTY}
    ${command} =    Catenate    python3 /root/matrix_client.py --host ${HOST} ${auth}    @{args}
    ${out}    ${err}    ${rc} =    Execute Command    ${command}    return_stderr=True    return_rc=True
    Should Be Equal As Integers    ${rc}    0    matrix_client ${args} failed: ${err}
    ${result} =    Evaluate    json.loads($out)    modules=json
    RETURN    ${result}

Unit of
    [Arguments]    ${module}    ${unit}    ${state}
    ${output} =    Execute Command    runagent -m ${module} systemctl --user is-active ${unit}
    Should Be Equal    ${output}    ${state}    ${unit} of ${module} is ${output}

Bot answers
    [Arguments]    ${bot}
    ${result} =    Matrix    bot-reply    @${bot}:${SERVER_NAME}    token=${admin_token}
    Should Not Be Empty    ${result}[reply]

Registered appservices
    ${config} =    Task    ${matrix_id}    get-configuration
    ${ids} =    Evaluate    sorted(item["id"] for item in $config["appservices"])
    RETURN    ${ids}

Install bridge
    [Arguments]    ${key}    ${payload}={}
    ${image} =    Image of    ${key}
    ${module} =    Add module    ${image}
    Set To Dictionary    ${bridge_ids}    ${key}=${module}
    Task    ${module}    configure-module    ${payload}
    ${config} =    Task    ${module}    get-configuration
    Should Be Equal    ${config}[bridge]    ${key}
    Should Be Equal    ${config}[matrix_module]    ${matrix_id}
    Should Be True    ${config}[registered]
    Unit of    ${module}    bridge.service    active
    RETURN    ${config}[bot]

*** Test Cases ***
Install the Matrix module
    Put File    ${CURDIR}/matrix_client.py    /root/matrix_client.py
    ${base} =    OperatingSystem.Get File    ${CURDIR}/base-image
    ${base} =    Strip String    ${base}
    ${id} =    Add module    ${base}
    Set Suite Variable    ${matrix_id}    ${id}
    Task    ${matrix_id}    configure-module    {"host": "${HOST}", "server_name": "${SERVER_NAME}", "lets_encrypt": false, "http2https": false, "federation": true, "wellknown_on_server_name": true}
    ${config} =    Task    ${matrix_id}    get-configuration
    ${login} =    Matrix    login    matrixadmin    ${config}[admin_password]
    Set Suite Variable    ${admin_token}    ${login}[access_token]

Bridge modules register and their bots answer
    ${whatsapp} =    Install bridge    whatsapp
    ${signal} =    Install bridge    signal
    ${telegram} =    Install bridge    telegram    {"telegram_api_id": "12345", "telegram_api_hash": "0123456789abcdef0123456789abcdef"}
    ${discord} =    Install bridge    discord
    # Steam runs in its own network namespace (pasta, host loopback mapped)
    ${steam} =    Install bridge    steam
    ${irc} =    Install bridge    heisenbridge
    ${ids} =    Registered appservices
    Should Be Equal    ${ids}    ${{["discord", "heisenbridge", "signal", "steam", "telegram", "whatsapp"]}}
    FOR    ${bot}    IN    ${whatsapp}    ${signal}    ${telegram}    ${discord}    ${steam}    ${irc}
        ${localpart} =    Evaluate    $bot.split(":")[0][1:]
        Bot answers    ${localpart}
    END

Telegram needs its API credentials
    ${image} =    Image of    telegram
    ${output}    ${rc} =    Execute Command    api-cli run module/${bridge_ids}[telegram]/configure-module --data '{"telegram_api_id": "", "telegram_api_hash": ""}'
    ...    return_rc=True
    Should Not Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    telegram_api_id_required

Bridges follow a reconfigured Matrix module
    # configure-module publishes matrix-changed; the bridges resync
    Task    ${matrix_id}    configure-module    {"host": "${HOST}", "server_name": "${SERVER_NAME}", "lets_encrypt": false, "http2https": false, "federation": true, "wellknown_on_server_name": true}
    Sleep    5s
    Unit of    ${bridge_ids}[whatsapp]    bridge.service    active
    Bot answers    whatsappbot

Bridge state is private and backed up
    ${module} =    Set Variable    ${bridge_ids}[signal]
    ${modes} =    Execute Command    runagent -m ${module} stat -c '%a' bridge.json data data/config.yaml data/registration.yaml
    Should Be Equal    ${modes}    600\n700\n600\n600
    ${rc} =    Execute Command    runagent -m ${module} module-dump-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    ${rc} =    Execute Command    runagent -m ${module} test -s data/bridge-backup.db
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    no database snapshot
    ${rc} =    Execute Command    runagent -m ${module} module-cleanup-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    ${include} =    Execute Command    runagent -m ${module} sh -c 'cat ../etc/state-include.conf'
    Should Contain    ${include}    state/data/bridge-backup.db

Module UI loads in cluster-admin
    [Tags]    ui
    Import Library    Browser
    New Browser    chromium    headless=True
    New Context    ignoreHTTPSErrors=True
    Login to cluster-admin
    Go To    https://${NODE_ADDR}/cluster-admin/#/apps/${bridge_ids}[whatsapp]?page=settings
    Wait For Elements State    iframe >>> h2 >> text="Settings"    visible    timeout=20s
    Wait For Elements State    iframe >>> input#bridge-bot    visible    timeout=30s
    Get Property    iframe >>> input#bridge-bot    value    ==    @whatsappbot:${SERVER_NAME}
    Get Property    iframe >>> input#matrix-module    value    ==    ${matrix_id}
    Take Screenshot    filename=${OUTPUT DIR}/browser/screenshot/1._Settings.png
    Close Browser

Removing a bridge module unregisters it
    ${rc} =    Execute Command    remove-module --no-preserve ${bridge_ids}[whatsapp]
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    ${ids} =    Registered appservices
    Should Not Contain    ${ids}    whatsapp
    Should Contain    ${ids}    signal
