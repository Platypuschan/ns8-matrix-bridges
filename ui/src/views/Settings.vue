<!--
  Copyright (C) 2022 Nethesis S.r.l.
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("settings.title") }}</h2>
      </cv-column>
    </cv-row>
    <cv-row v-if="error.getConfiguration">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-configuration')"
          :description="error.getConfiguration"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <h4 class="mg-bottom">{{ bridgeName }}</h4>
          <p class="mg-bottom helper">
            {{ $t("settings.description") }}
          </p>
          <cv-text-input
            id="matrix-module"
            :label="$t('settings.matrix_module')"
            :value="matrixModule || $t('settings.not_connected')"
            readonly
            class="mg-bottom"
          />
          <cv-text-input
            v-if="bot"
            id="bridge-bot"
            :label="$t('settings.bot')"
            :helper-text="$t('settings.bot_helper')"
            :value="bot"
            readonly
            class="mg-bottom"
          />
          <cv-form @submit.prevent="configureModule">
            <template v-if="bridge === 'telegram'">
              <p class="mg-bottom helper">
                {{ $t("settings.telegram_description") }}
              </p>
              <cv-text-input
                id="telegram-api-id"
                :label="$t('settings.telegram_api_id')"
                v-model.trim="telegramApiId"
                class="mg-bottom"
                :invalid-message="$t(error.telegram_api_id)"
                :disabled="busy"
                ref="telegram_api_id"
              />
              <cv-text-input
                id="telegram-api-hash"
                :label="$t('settings.telegram_api_hash')"
                :helper-text="
                  telegramApiHashSet ? $t('settings.telegram_api_hash_set') : ''
                "
                v-model.trim="telegramApiHash"
                type="password"
                autocomplete="off"
                class="mg-bottom"
                :invalid-message="$t(error.telegram_api_hash)"
                :disabled="busy"
                ref="telegram_api_hash"
              />
            </template>
            <cv-text-input
              v-if="bridge === 'heisenbridge'"
              id="heisenbridge-owner"
              :label="$t('settings.heisenbridge_owner')"
              :helper-text="$t('settings.heisenbridge_owner_helper')"
              placeholder="@alice:example.org"
              v-model.trim="heisenbridgeOwner"
              class="mg-bottom"
              :invalid-message="$t(error.heisenbridge_owner)"
              :disabled="busy"
              ref="heisenbridge_owner"
            />
            <NsInlineNotification
              v-if="error.matrix_module"
              kind="error"
              :title="$t('settings.' + error.matrix_module)"
              :showCloseButton="false"
            />
            <NsInlineNotification
              v-if="error.configureModule"
              kind="error"
              :title="$t('action.configure-module')"
              :description="error.configureModule"
              :showCloseButton="false"
            />
            <NsButton
              kind="primary"
              :icon="Save20"
              :loading="loading.configureModule"
              :disabled="busy"
              >{{
                matrixModule ? $t("settings.save") : $t("settings.connect")
              }}</NsButton
            >
          </cv-form>
        </cv-tile>
      </cv-column>
    </cv-row>
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  IconService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

export default {
  name: "Settings",
  mixins: [
    TaskService,
    IconService,
    UtilService,
    QueryParamService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("settings.title") + " - " + this.appName;
  },
  data() {
    return {
      q: {
        page: "settings",
      },
      urlCheckInterval: null,
      bridge: "",
      bridgeName: "",
      bot: "",
      matrixModule: "",
      telegramApiId: "",
      telegramApiHash: "",
      telegramApiHashSet: false,
      heisenbridgeOwner: "",
      loading: {
        getConfiguration: false,
        configureModule: false,
      },
      error: {
        getConfiguration: "",
        configureModule: "",
        matrix_module: "",
        telegram_api_id: "",
        telegram_api_hash: "",
        heisenbridge_owner: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    busy() {
      return this.loading.getConfiguration || this.loading.configureModule;
    },
  },
  created() {
    this.getConfiguration();
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    clearInterval(this.urlCheckInterval);
    next();
  },
  methods: {
    async runTask(action, data, handlers, extra) {
      const eventId = this.getUuid();
      this.core.$root.$once(`${action}-aborted-${eventId}`, handlers.aborted);
      this.core.$root.$once(
        `${action}-completed-${eventId}`,
        handlers.completed
      );
      if (handlers.validationFailed) {
        this.core.$root.$once(
          `${action}-validation-failed-${eventId}`,
          handlers.validationFailed
        );
      }
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action,
          data,
          extra: {
            title: this.$t("action." + action),
            isNotificationHidden: true,
            ...extra,
            eventId,
          },
        })
      );
      return res[0];
    },
    focusElement(ref) {
      const element = this.$refs[ref];
      if (element && element.focus) {
        element.focus();
      }
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const err = await this.runTask("get-configuration", undefined, {
        aborted: this.getConfigurationAborted,
        completed: this.getConfigurationCompleted,
      });
      if (err) {
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
      }
    },
    getConfigurationAborted() {
      this.error.getConfiguration = this.$t("error.generic_error");
      this.loading.getConfiguration = false;
    },
    getConfigurationCompleted(taskContext, taskResult) {
      const config = taskResult.output;
      this.bridge = config.bridge;
      this.bridgeName = config.name;
      this.bot = config.bot;
      this.matrixModule = config.matrix_module;
      this.telegramApiId = config.telegram_api_id;
      this.telegramApiHash = "";
      this.telegramApiHashSet = config.telegram_api_hash_set;
      this.heisenbridgeOwner = config.heisenbridge_owner;
      this.loading.getConfiguration = false;
    },
    async configureModule() {
      this.clearErrors(this);
      this.loading.configureModule = true;
      const data = {};
      if (this.bridge === "telegram") {
        data.telegram_api_id = this.telegramApiId;
        data.telegram_api_hash = this.telegramApiHash;
      }
      if (this.bridge === "heisenbridge") {
        data.heisenbridge_owner = this.heisenbridgeOwner;
      }
      const err = await this.runTask(
        "configure-module",
        data,
        {
          aborted: this.configureModuleAborted,
          completed: this.configureModuleCompleted,
          validationFailed: this.configureModuleValidationFailed,
        },
        {
          title: this.$t("settings.instance_configuration", {
            instance: this.instanceName,
          }),
          description: this.$t("settings.configuring"),
          isNotificationHidden: false,
        }
      );
      if (err) {
        this.error.configureModule = this.getErrorMessage(err);
        this.loading.configureModule = false;
      }
    },
    configureModuleValidationFailed(validationErrors) {
      this.loading.configureModule = false;
      for (const validationError of validationErrors) {
        const param = validationError.parameter;
        this.error[param] =
          param === "matrix_module"
            ? validationError.error
            : "settings." + validationError.error;
        if (param !== "matrix_module") {
          this.$nextTick(() => this.focusElement(param));
        }
      }
    },
    configureModuleAborted() {
      this.error.configureModule = this.$t("error.generic_error");
      this.loading.configureModule = false;
    },
    configureModuleCompleted() {
      this.loading.configureModule = false;
      this.getConfiguration();
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom {
  margin-bottom: $spacing-06;
}

.helper {
  max-width: 38rem;
}
</style>
