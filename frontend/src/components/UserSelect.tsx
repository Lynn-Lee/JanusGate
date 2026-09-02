import { Select } from 'antd';
import { useAuth } from '../auth/AuthContext';
import { useApiData } from '../pages/pageUtils';

export type DirectoryUser = {
  id: number | string;
  username: string;
  display_name?: string;
};

type UserSelectProps = {
  value?: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

function userLabel(user: DirectoryUser): string {
  const username = user.username;
  return user.display_name ? `${username}（${user.display_name}）` : username;
}

export function UserSelect({
  value,
  onChange,
  disabled,
  placeholder = '请选择用户'
}: UserSelectProps) {
  const { api } = useAuth();
  const users = useApiData(() => api.get<{ items: DirectoryUser[]; total: number }>(
    '/api/v1/users/'
  ), []);
  const options = (users.data?.items ?? []).map((item) => ({
    value: String(item.id),
    label: userLabel(item),
    username: item.username
  }));

  return (
    <Select
      value={value}
      onChange={onChange}
      disabled={disabled}
      placeholder={placeholder}
      loading={users.loading}
      options={options}
      showSearch
      optionFilterProp="label"
      filterOption={(input, option) =>
        String(option?.username ?? option?.label ?? '').toLowerCase().includes(input.trim().toLowerCase())
      }
      style={{ width: '100%' }}
    />
  );
}
