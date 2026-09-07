import { Alert, Button, Card, Form, Input, Space, Switch, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';

type OidcSettings = {
  enabled: boolean;
  display_name: string;
  issuer_url: string;
  client_id: string;
  client_secret_configured: boolean;
  scopes: string;
  callback_url: string;
};

type OidcFormValues = {
  enabled: boolean;
  display_name: string;
  issuer_url: string;
  client_id: string;
  client_secret?: string;
  scopes: string;
};

export function OidcSettingsCard() {
  const { api } = useAuth();
  const messages = useApiMessage();
  const settings = useApiData(() => api.get<OidcSettings>('/api/v1/auth/oidc/settings'), []);
  const [form] = Form.useForm<OidcFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  useEffect(() => {
    if (!settings.data) {
      return;
    }
    form.setFieldsValue({
      enabled: settings.data.enabled,
      display_name: settings.data.display_name,
      issuer_url: settings.data.issuer_url,
      client_id: settings.data.client_id,
      client_secret: '',
      scopes: settings.data.scopes || 'openid profile email'
    });
  }, [form, settings.data]);

  const save = async (values: OidcFormValues) => {
    setSubmitting(true);
    setSubmitError('');
    try {
      const saved = await api.put<OidcSettings>('/api/v1/auth/oidc/settings', {
        enabled: values.enabled,
        display_name: values.display_name,
        issuer_url: values.issuer_url,
        client_id: values.client_id,
        client_secret: values.client_secret ?? '',
        scopes: values.scopes || 'openid profile email'
      });
      form.setFieldsValue({
        enabled: saved.enabled,
        display_name: saved.display_name,
        issuer_url: saved.issuer_url,
        client_id: saved.client_id,
        client_secret: '',
        scopes: saved.scopes
      });
      settings.reload();
      messages.success('OIDC 配置已保存');
    } catch (err: unknown) {
      setSubmitError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const callbackUrl = settings.data?.callback_url ?? '';

  return (
    <Card title="OIDC">
      {settings.loading ? <LoadingState /> : null}
      {settings.error ? <ErrorState message={settings.error} onRetry={settings.reload} /> : null}
      {!settings.loading && !settings.error ? (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            用邮箱匹配已有用户；没有对应账号则无法登录
          </Typography.Paragraph>
          {submitError ? <Alert showIcon type="error" message={submitError} /> : null}
          <Form form={form} layout="vertical" onFinish={(values) => void save(values)}>
            <Form.Item label="启用" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              label="显示名称"
              name="display_name"
              rules={[{ required: true, message: '请输入显示名称' }]}
            >
              <Input placeholder="例如：公司账号" />
            </Form.Item>
            <Form.Item
              label="Issuer URL"
              name="issuer_url"
              rules={[{ required: true, message: '请输入 Issuer URL' }]}
            >
              <Input placeholder="https://idp.example.com" />
            </Form.Item>
            <Form.Item
              label="Client ID"
              name="client_id"
              rules={[{ required: true, message: '请输入 Client ID' }]}
            >
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item label="Client Secret" name="client_secret">
              <Input.Password
                autoComplete="new-password"
                placeholder={
                  settings.data?.client_secret_configured ? '已保存，输入则更新' : '请输入 Client Secret'
                }
              />
            </Form.Item>
            <Form.Item label="授权范围" name="scopes">
              <Input placeholder="openid profile email" />
            </Form.Item>
            <Form.Item label="回调地址">
              <Space.Compact style={{ width: '100%' }}>
                <Input readOnly value={callbackUrl} />
                <Button
                  onClick={() => {
                    if (!callbackUrl) {
                      return;
                    }
                    void navigator.clipboard.writeText(callbackUrl).then(
                      () => messages.success('已复制回调地址'),
                      () => messages.error('复制失败')
                    );
                  }}
                >
                  复制
                </Button>
              </Space.Compact>
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting}>
              保存 OIDC 配置
            </Button>
          </Form>
        </Space>
      ) : null}
    </Card>
  );
}
