import { Alert, Button, Empty, Skeleton } from 'antd';

export function LoadingState({ title = '正在加载数据' }: { title?: string }) {
  return <Skeleton active paragraph={{ rows: 4 }} title={{ width: title }} />;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      message="请求失败"
      description={message}
      action={onRetry ? <Button onClick={onRetry}>重试</Button> : undefined}
    />
  );
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return <Empty description={<span>{title}</span>} image={Empty.PRESENTED_IMAGE_SIMPLE}>{description}</Empty>;
}
