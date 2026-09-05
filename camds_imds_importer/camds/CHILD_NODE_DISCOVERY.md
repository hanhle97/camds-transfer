# CAMDS child-node discovery — 2026-09-05

Khảo sát trực tiếp giao diện English. Danh mục: [child_node_selectors.json](child_node_selectors.json).
Chỉ thao tác cây khảo sát chưa lưu; đã rời cây và trở lại Create / MDS.
Không bấm Save, Delete, Send hoặc Submit. Việc thêm node vẫn cấp ID hiển thị;
không thể suy ra trạng thái lưu phía máy chủ chỉ từ ID.

| Nhu cầu | Nút / form đã xác minh |
|---|---|
| Add child Component | `img[title="Add Component"]`; thêm trực tiếp dưới node đang chọn |
| Quantity | Form item có label chính xác `Quantity`, input text, mặc định 1 |
| Add Material | `img[title="Add Metarial"]`; mở dialog `Detail`, tab `Material`, tìm rồi chọn ID/version và Confirm |
| Material dưới Component | Label `Mass`, input text, mặc định 0 g; không phải percentage |
| Add Substance | `img[title="Add Substance"]` trên Material của mình; dialog `Detail`, tab `Basic Substance`, tìm CAS, chọn dòng và Confirm |
| Percentage | Form item `Proportion`, có From/To, Fixed và Rest |
| Save | Nút role button, tên chính xác `Save`, trong `.handle`, ngoài tabpanel Details |

## Các điểm cần phân biệt

- Add Material là **gắn tham chiếu Material có sẵn**, không mở form tạo Material
  mới. Sau khi gắn published Material, tên/mã/phân loại và thành phần không sửa
  được; có Mass, Application và recyclate. Không có Add Substance trên node này.
- Add Substance đã được thử trên Material riêng có classification 1.1.1.
  Form Substance có CAS, English Name và Proportion; không có Quantity.
- Đã nhập Fixed 10, From 5 / To 10 và đọc lại trực tiếp; đã chọn Rest.
  Chuyển chế độ làm dữ liệu chế độ trước trở về 0. Chọn chế độ trước khi điền.
- Range có đúng hai input text trong wrapper radio: From là index 0, To là 1.
  Fixed có đúng một input. Rest không có input; không điền phần trăm vào Rest.
- Radio wrapper có `role="radio"`; input radio bên trong có `aria-hidden=true`
  và value lần lượt 1=range, 2=fixed, 3=rest. Click wrapper, không click input ẩn.
- Quantity mới xác minh form và mặc định; chưa thử validation số lượng.
- Không kết luận Average/Rest được tính đúng hoặc dữ liệu đã lưu: chưa thử blur,
  Save hoặc reload dữ liệu.

## Locator mẫu Python

```python
details = page.get_by_role('tabpanel', name='Details', exact=True)
quantity = details.locator('.el-form-item:has(> .el-form-item__label:text-is("Quantity")) input')
proportion = details.locator('.el-form-item:has(> .el-form-item__label:text-is("Proportion"))')
fixed = proportion.locator('label[role="radio"]:has(input[type="radio"][value="2"])')
range_mode = proportion.locator('label[role="radio"]:has(input[type="radio"][value="1"])')
rest = proportion.locator('label[role="radio"]:has(input[type="radio"][value="3"])')
# Chỉ là locator; thao tác thêm/lưu cần được gọi rõ ràng trong workflow.
fixed_value = fixed.locator('input[type="text"]')
from_value = range_mode.locator('input[type="text"]').nth(0)
to_value = range_mode.locator('input[type="text"]').nth(1)
save = page.locator('.handle').get_by_role('button', name='Save', exact=True)
```

## Save từng node: chưa có bằng chứng

Quan sát cùng một nút Save ở thanh thao tác chung khi chọn root, child Component,
Material tham chiếu hoặc Substance. Không thấy nút Save riêng bên trong form node.
DOM không xác định được Save lưu node đang chọn hay cả cây. Vì vậy chưa được triển
khai thuật toán bấm Save sau từng node dựa trên giả định này. Cần kiểm thử lưu một
bản nháp hợp lệ và mở lại để đối chiếu cây, Quantity, Mass và Proportion.

Khảo sát này chỉ bổ sung bằng chứng và selector, chưa thay đổi luồng app.
