#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export AWS_PAGER=""
region="${AWS_REGION:-us-east-1}"
prefix="${1:-cad-sis3}"
tag="${2:-$(git rev-parse HEAD)}"
network="${SIS3_NETWORK_STACK:-$prefix-network}"
messaging="${SIS3_MESSAGING_STACK:-$prefix-messaging}"
images="${SIS3_IMAGE_STACK:-$prefix-images}"
app="${SIS3_APP_STACK:-$prefix-app}"

deploy() {
  local stack=$1 template=$2
  shift 2
  aws cloudformation deploy --region "$region" --stack-name "$stack" \
    --template-file "$template" --no-fail-on-empty-changeset "$@"
}

output() {
  local value
  value=$(aws cloudformation describe-stacks --region "$region" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text)
  if [[ -z "$value" || "$value" == None ]]; then
    printf 'Missing stack output %s from %s\n' "$2" "$1" >&2
    return 1
  fi
  printf '%s\n' "$value"
}

if [[ "${SIS3_SHARED_BASE:-0}" != 1 ]]; then
  deploy "$network" infra/network.yaml --parameter-overrides "EnvName=$prefix"
  deploy "$images" infra/ecr.yaml
fi

deploy "$messaging" infra/messaging.yaml
repository=$(output "$images" RepositoryUri)
image="$repository:$tag"
case "$(docker info --format '{{.Architecture}}')" in
  x86_64|amd64) architecture=X86_64 ;;
  aarch64|arm64) architecture=ARM64 ;;
  *) printf 'Unsupported Docker architecture\n' >&2; exit 1 ;;
esac

existing_tag=$(aws ecr list-images --region "$region" --repository-name "${repository#*/}" \
  --query "imageIds[?imageTag=='$tag'].imageTag | [0]" --output text)
if [[ "$existing_tag" != "$tag" ]]; then
  aws ecr get-login-password --region "$region" |
    docker login --username AWS --password-stdin "${repository%%/*}"
  docker build -t "$image" app
  docker push "$image"
fi

parameters=(
  "ImageRepositoryUri=$repository"
  "ImageTag=$tag"
  "CpuArchitecture=$architecture"
)
for key in VpcId PublicSubnetAId PublicSubnetBId PrivateSubnetAId PrivateSubnetBId; do
  parameters+=("$key=$(output "$network" "$key")")
done
for key in PaymentQueueUrl PaymentQueueArn ProcessedTableName ProcessedTableArn AccountsTableName AccountsTableArn; do
  output_key=$key
  case "$key" in
    PaymentQueueUrl) output_key=QueueUrl ;;
    PaymentQueueArn) output_key=QueueArn ;;
  esac
  parameters+=("$key=$(output "$messaging" "$output_key")")
done

deploy "$app" infra/app.yaml --capabilities CAPABILITY_IAM --parameter-overrides "${parameters[@]}"
service_url=$(output "$app" ServiceUrl)
printf 'Application URL: %s\n' "$service_url"
curl -fsS --retry 6 --retry-all-errors --retry-delay 3 --max-time 10 \
  -w '\nHTTP %{http_code}\n' "$service_url/health"
